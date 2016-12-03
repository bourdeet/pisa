#!/usr/bin/env python

# authors: J.L. Lanfranchi, P.Eller, and S. Wren
# email:   jll1062+pisa@phys.psu.edu
# date:    March 20, 2016
"""
Hypothesis testing: How do two hypotheses compare for describing MC or data?

This script/module computes significances, etc. from the logfiles recorded by
the `hypo_testing.py` script.

"""


from __future__ import division

from argparse import ArgumentParser
from collections import OrderedDict
import os
import matplotlib.pyplot as plt
plt.rcParams['text.usetex'] = True
import numpy as np
import re

from scipy.stats import norm, spearmanr

from pisa import ureg
from pisa.analysis.hypo_testing import Labels
from pisa.core.param import Param, ParamSet
from pisa.utils.fileio import from_file, nsort
from pisa.utils.log import set_verbosity, logging


__all__ = ['extract_trials', 'extract_fit', 'parse_args', 'main']


def make_pretty(label):
    '''
    Takes the labels used in the objects and turns them in to something nice
    for plotting. This can never truly be exhaustive, but it definitely does 
    the trick. If something looks ugly add it to this function!
    '''
    pretty_labels = {}
    pretty_labels["atm_muon_scale"] = r"Muon Background Scale"
    pretty_labels["nue_numu_ratio"] = r"$\nu_e/\nu_{\mu}$ Ratio"
    pretty_labels["Barr_uphor_ratio"] = r"Barr Up/Horizontal Ratio"
    pretty_labels["Barr_nu_nubar_ratio"] = r"Barr $\nu/\bar{\nu}$ Ratio"
    pretty_labels["delta_index"] = r"Atmospheric Index Change"
    pretty_labels["theta13"] = r"$\theta_{13}$"
    pretty_labels["theta23"] = r"$\theta_{23}$"
    pretty_labels["deltam31"] = r"$\Delta m^2_{31}$"
    pretty_labels["aeff_scale"] = r"$A_{\mathrm{eff}}$ Scale"
    pretty_labels["Genie_Ma_QE"] = r"GENIE $M_{A}^{QE}$"
    pretty_labels["Genie_Ma_RES"] = r"GENIE $M_{A}^{Res}$"
    pretty_labels["dom_eff"] = r"DOM Efficiency"
    pretty_labels["hole_ice"] = r"Hole Ice"
    pretty_labels["hole_ice_fwd"] = r"Hole Ice Forward"
    pretty_labels["degree"] = r"$^\circ$"
    pretty_labels["radians"] = r"rads"
    pretty_labels["electron_volt ** 2"] = r"$\mathrm{eV}^2$"
    pretty_labels["llh"] = r"Likelihood"
    pretty_labels["chi2"] = r"$\chi^2$"
    pretty_labels["mod_chi2"] = r"Modified $\chi^2$"
    if label not in pretty_labels.keys():
        logging.warn("I don't know what to do with %s. Returning as is."%label)
        return label
    return pretty_labels[label]

def get_num_rows(data, omit_metric=False):
    '''
    Calculates the number of rows for multiplots based on the number of 
    systematics.
    '''
    if omit_metric:
        num_rows = int((len(data.keys())-1)/4)
    else:
        num_rows = int(len(data.keys())/4)
    if len(data.keys())%4 != 0:
        num_rows += 1
    return num_rows


def extract_injval(injparams, systkey, data_label, hypo_label, injlabel):
    '''
    Extracts the injected value and modifies it based on the 
    hypothesis/fiducial fit being considered. The label associated with this 
    is then modified accordingly.
    '''
    if systkey == 'deltam31':
        if hypo_label == data_label:
            injval = float(injparams[systkey].split(' ')[0])
        else:
            injval = -1*float(injparams[systkey].split(' ')[0])
            injlabel += r' ($\times-1$)'
            
    else:
        injval = float(injparams[systkey].split(' ')[0])

    if (injval < 1e-2) and (injval != 0.0):
        injlabel += ' = %.3e'%injval
    else:    
        injlabel += ' = %.3g'%injval

    return injval, injlabel


def extract_gaussian(prior_string, units):
    '''
    Parses the string for the Gaussian priors that comes from the config 
    summary file in the logdir. This should account for dimensions though is 
    only tested with degrees.
    '''
    if units == 'dimensionless':
        parse_string = ('gaussian prior: stddev=(.*)'
                        ' , maximum at (.*)')
        bits = re.match(
            parse_string,
            prior_string,
            re.M|re.I
        )
        stddev = float(bits.group(1))
        maximum = float(bits.group(2))
    else:
        parse_string = ('gaussian prior: stddev=(.*) (.*)'
                        ', maximum at (.*) (.*)')
        bits = re.match(
            parse_string,
            prior_string,
            re.M|re.I
        )
        stddev = float(bits.group(1))
        maximum = float(bits.group(3))

    return stddev, maximum
    

def extract_trials(logdir, fluctuate_fid, fluctuate_data=False):
    """Extract and aggregate analysis results.

    Parameters
    ----------
    logdir : string
        Path to logging directory where files are stored. This should contain
        e.g. the "config_summary.json" file.

    fluctuate_fid : bool
        Whether the trials you're interested in applied fluctuations to the
        fiducial-fit Asimov distributions. `fluctuate_fid` False is equivalent
        to specifying an Asimov analysis (so long as the metric used was
        chi-squared).

    fluctuate_data : bool
        Whether the trials you're interested in applied fluctuations to the
        (toy) data. This is invalid if actual data was processed.

    Note that a single `logdir` can have different kinds of analyses run and
    results be logged within, so `fluctuate_fid` and `fluctuate_data` allows
    these to be separated from one another.

    """
    logdir = os.path.expanduser(os.path.expandvars(logdir))
    config_summary_fpath = os.path.join(logdir, 'config_summary.json')
    cfg = from_file(config_summary_fpath)

    data_is_data = cfg['data_is_data']
    if data_is_data and fluctuate_data:
        raise ValueError('Analysis was performed on data, so `fluctuate_data`'
                         ' is not supported.')

    # Get naming scheme
    labels = Labels(
        h0_name=cfg['h0_name'], h1_name=cfg['h1_name'],
        data_name=cfg['data_name'], data_is_data=data_is_data,
        fluctuate_data=fluctuate_data, fluctuate_fid=fluctuate_fid
    )

    all_params = {}
    all_params['h0_params'] = {}
    all_params['h1_params'] = {}
    parse_string = ('(.*)=(.*); prior=(.*),'
                    ' range=(.*), is_fixed=(.*),'
                    ' is_discrete=(.*); help="(.*)"')
    for param_string in cfg['h0_params']:
        bits = re.match(parse_string, param_string, re.M|re.I)
        if bits.group(5) == 'False':
            all_params['h0_params'][bits.group(1)] = {}
            all_params['h0_params'][bits.group(1)]['value'] = bits.group(2)
            all_params['h0_params'][bits.group(1)]['prior'] = bits.group(3)
            all_params['h0_params'][bits.group(1)]['range'] = bits.group(4)
    for param_string in cfg['h1_params']:
        bits = re.match(parse_string, param_string, re.M|re.I)
        if bits.group(5) == 'False':
            all_params['h1_params'][bits.group(1)] = {}
            all_params['h1_params'][bits.group(1)]['value'] = bits.group(2)
            all_params['h1_params'][bits.group(1)]['prior'] = bits.group(3)
            all_params['h1_params'][bits.group(1)]['range'] = bits.group(4)

    #for key in labels.dict.keys():
    #    print key

    # Find all relevant data dirs, and from each extract the fiducial fit(s)
    # information contained
    data_sets = OrderedDict()
    for basename in nsort(os.listdir(logdir)):
        m = labels.subdir_re.match(basename)
        if m is None:
            continue

        if fluctuate_data:
            data_ind = int(m.groupdict()['data_ind'])
            dset_label = data_ind
        else:
            dset_label = labels.data_prefix
            if not labels.data_name in [None, '']:
                dset_label += '_' + labels.data_name
            if not labels.data_suffix in [None, '']:
                dset_label += '_' + labels.data_suffix

        lvl2_fits = OrderedDict()
        lvl2_fits['h0_fit_to_data'] = None
        lvl2_fits['h1_fit_to_data'] = None

        # Account for failed jobs. Get the set of file numbers that exist
        # for all h0 an h1 combinations
        file_nums = OrderedDict()
        subdir = os.path.join(logdir, basename)
        for fnum, fname in enumerate(nsort(os.listdir(subdir))):
            fpath = os.path.join(subdir, fname)
            for x in ['0', '1']:
                for y in ['0','1']:
                    k = 'h{x}_fit_to_h{y}_fid'.format(x=x, y=y)
                    r = labels.dict[k + '_re']
                    m = r.match(fname)
                    if m is None:
                        continue
                    if fluctuate_fid:
                        fid_label = int(m.groupdict()['fid_ind'])
                    else:
                        fid_label = labels.fid
                    if k not in file_nums:
                        file_nums[k] = []
                    file_nums[k].append(fid_label)
                    break

        set_file_nums = []
        for hypokey in file_nums.keys():
            if len(set_file_nums) == 0:
                set_file_nums = set(file_nums[hypokey])
            else:
                set_file_nums = set_file_nums.intersection(file_nums[hypokey])

        for fnum, fname in enumerate(nsort(os.listdir(subdir))):
            fpath = os.path.join(subdir, fname)
            for x in ['0', '1']:
                k = 'h{x}_fit_to_data'.format(x=x)
                if fname == labels.dict[k]:
                    lvl2_fits[k] = extract_fit(fpath, 'metric_val')
                    break
                # Also extract fiducial fits if needed
                if 'toy' in dset_label:
                    ftest = ('hypo_%s_fit_to_%s.json'
                             %(labels.dict['h{x}_name'.format(x=x)],
                               dset_label))
                    if fname == ftest:
                        k = 'h{x}_fit_to_{y}'.format(x=x,y=dset_label)
                        lvl2_fits[k] = extract_fit(
                            fpath,
                            ['metric_val', 'params']
                        )
                        break
                k = 'h{x}_fit_to_{y}'.format(x=x, y=dset_label)
                for y in ['0','1']:
                    k = 'h{x}_fit_to_h{y}_fid'.format(x=x, y=y)
                    r = labels.dict[k + '_re']
                    m = r.match(fname)
                    if m is None:
                        continue
                    if fluctuate_fid:
                        fid_label = int(m.groupdict()['fid_ind'])
                    else:
                        fid_label = labels.fid
                    if k not in lvl2_fits:
                        lvl2_fits[k] = OrderedDict()
                    if fid_label in set_file_nums:
                        lvl2_fits[k][fid_label] = \
                            extract_fit(fpath,
                                        ['metric', 'metric_val','params'])
                    break
        data_sets[dset_label] = lvl2_fits
        data_sets[dset_label]['params'] = \
            extract_fit(fpath, ['params'])['params']
    return data_sets, all_params, labels


def extract_fit(fpath, keys=None):
    """Extract fit info from a file.

    Parameters
    ----------
    fpath : string
        Path to the file

    keys : None, string, or iterable of strings
        Keys to extract. If None, all keys are extracted.

    """
    info = from_file(fpath)
    if keys is None:
        return info
    if isinstance(keys, basestring):
        keys = [keys]
    for key in info.keys():
        if key not in keys:
            info.pop(key)
    return info


def extract_fid_data(data_sets):
    '''
    Takes the data sets returned by the extract_trials function and extracts 
    the data on the fiducial fits.

    TODO (?) - This works in the case of all MC, but I don't know about data.
    '''
    fid_values = {}
    for injkey in data_sets.keys():
        fid_values[injkey] = {}
        for datakey in data_sets[injkey]:
            if ('toy' in datakey) or ('data' in datakey):
                fid_values[injkey][datakey] \
                    = data_sets[injkey].pop(datakey)
    return fid_values


def extract_data(data):
    '''
    Takes the data sets returned by the extract_trials function and turns 
    them in to a format used by all of the plotting functions.
    '''
    values = {}
    for injkey in data.keys():
        values[injkey] = {}
        alldata = data[injkey]
        paramkeys = alldata['params'].keys()
        for datakey in alldata.keys():
            if datakey is not 'params':
                values[injkey][datakey] = {}
                values[injkey][datakey]['metric_val'] = {}
                values[injkey][datakey]['metric_val']['vals'] = []
                for paramkey in paramkeys:
                    values[injkey][datakey][paramkey] = {}
                    values[injkey][datakey][paramkey]['vals'] = []
                trials = alldata[datakey]
                for trial_num in trials.keys():
                    trial = trials[trial_num]
                    values[injkey][datakey]['metric_val']['vals'] \
                        .append(trial['metric_val'])
                    values[injkey][datakey]['metric_val']['type'] \
                        = trial['metric']
                    values[injkey][datakey]['metric_val']['units'] \
                        = 'dimensionless'
                    param_vals = trial['params']
                    for param_name in param_vals.keys():
                        val = param_vals[param_name].split(' ')[0]
                        units = param_vals[param_name] \
                            .split(val+' ')[-1]
                        values[injkey][datakey][param_name]['vals'] \
                            .append(float(val))
                        values[injkey][datakey][param_name]['units'] \
                            = units
    return values


def make_llr_plots(data, labels, detector, selection, outdir):
    '''
    Does what you think. Takes the data and makes LLR distributions. These are 
    then saved to the requested outdir within a folder labelled 
    "LLRDistributions".

    TODO - Significance calculation. This means calculating p-values and then 
    appending the value to the plot. Probably should come up with a good way 
    of storing this information rather than just printing it to terminal.
    '''
    outdir = os.path.join(outdir,'LLRDistributions')
    if not os.path.exists(outdir):
        logging.info('Making output directory %s'%outdir)
        os.makedirs(outdir)

    h0_fit_to_h0_fid_metrics = np.array(data['h0_fit_to_h0_fid']['metric_val'])
    h1_fit_to_h0_fid_metrics = np.array(data['h1_fit_to_h0_fid']['metric_val'])
    h0_fit_to_h1_fid_metrics = np.array(data['h0_fit_to_h1_fid']['metric_val'])
    h1_fit_to_h1_fid_metrics = np.array(data['h1_fit_to_h1_fid']['metric_val'])

    num_trials = len(h0_fit_to_h0_fid_metrics)
    
    LLRh0 = h0_fit_to_h0_fid_metrics - h1_fit_to_h0_fid_metrics
    LLRh1 = h0_fit_to_h1_fid_metrics - h1_fit_to_h1_fid_metrics

    minLLR = min(min(LLRh0), min(LLRh1))
    maxLLR = max(max(LLRh0), max(LLRh1))
    rangeLLR = maxLLR - minLLR
    binning = np.linspace(minLLR - 0.1*rangeLLR,
                          maxLLR + 0.1*rangeLLR,
                          num_trials/3)
    binwidth = binning[1]-binning[0]

    LLRh0hist, LLRh0binedges = np.histogram(LLRh0,bins=binning)
    LLRh1hist, LLRh1binedges = np.histogram(LLRh1,bins=binning)

    LLRhistmax = max(max(LLRh0hist),max(LLRh1hist))

    inj_name = labels['data_name']
    h0_name = labels['h0_name']
    h1_name = labels['h1_name']

    plot_labels = []
    plot_labels.append(
        (r"%s best fit - $\log\left[\mathcal{L}\left(\mathcal{H}_{%s}\right)/"
         r"\mathcal{L}\left(\mathcal{H}_{%s}\right)\right]$"
         %(h0_name, h0_name, h1_name))
    )
    plot_labels.append(
        (r"%s best fit - $\log\left[\mathcal{L}\left(\mathcal{H}_{%s}\right)/"
         r"\mathcal{L}\left(\mathcal{H}_{%s}\right)\right]$"
         %(h1_name, h0_name, h1_name))
    )
    plot_title = ('%s %s Event Selection LLR Distributions for true '
                  '%s (%i trials)'
                  %(detector,selection,inj_name,num_trials))

    # Factor with which to make everything visible
    plot_scaling_factor = 1.55

    plt.hist(LLRh0,bins=binning,color='r',histtype='step')
    plt.hist(LLRh1,bins=binning,color='b',histtype='step')
    plt.xlabel(r'Log-Likelihood Ratio')
    plt.ylabel(r'Number of Trials (per %.2f)'%binwidth)
    plt.ylim(0,plot_scaling_factor*LLRhistmax)
    plt.legend(plot_labels, loc='upper left')
    plt.title(plot_title)
    filename = 'true_%s_%s_%s_LLRDistribution_%i_Trials.png'%(
        inj_name, detector, selection, num_trials
    )
    plt.savefig(os.path.join(outdir,filename))
    plt.close()


def plot_individual_posterior(data, injparams, altparams, all_params, labels,
                              injlabel, altlabel, systkey, fhkey,
                              subplotnum=None):
    '''
    This function will use matplotlib to make a histogram of the vals contained
    in data. The injected value will be plotted along with, where appropriate,
    the "wrong hypothesis" fiducial fit and the prior. The axis labels and the
    legend are taken care of in here. The optional subplotnum argument can be
    given in the combined case so that the y-axis label only get put on when 
    appropriate.
    '''

    if systkey == 'metric_val':
        metric_type = data['type']
    systvals = np.array(data['vals'])
    units = data['units']

    hypo = fhkey.split('_')[0]
    fid = fhkey.split('_')[-2]
                
    plt.hist(systvals, bins=10)

    # Add injected and alternate fit lines
    if not systkey == 'metric_val':
        injval, injlabelproper = extract_injval(
            injparams = injparams,
            systkey = systkey,
            data_label = labels['data_name'],
            hypo_label = labels['%s_name'%hypo],
            injlabel = injlabel
        )
        plt.axvline(
            injval,
            color='r',
            linewidth=2,
            label=injlabelproper
        )
        if not labels['%s_name'%fid] == labels['data_name']:
            altval, altlabelproper = extract_injval(
                injparams = altparams,
                systkey = systkey,
                data_label = labels['%s_name'%fid],
                hypo_label = labels['%s_name'%hypo],
                injlabel = altlabel
            )
            plt.axvline(
                altval,
                color='g',
                linewidth=2,
                label=altlabelproper
            )

    # Add shaded region for prior, if appropriate
    # TODO - Deal with non-gaussian priors
    wanted_params = all_params['%s_params'%hypo]
    for param in wanted_params.keys():
        if param == systkey:
            if 'gaussian' in wanted_params[param]['prior']:
                stddev, maximum = extract_gaussian(
                    prior_string = wanted_params[param]['prior'],
                    units = units
                )
                currentxlim = plt.xlim()
                if (stddev < 1e-2) and (stddev != 0.0):
                    priorlabel = (r'Gaussian Prior '
                                  '($%.3e\pm%.3e$)'%(maximum,stddev))
                else:
                    priorlabel = (r'Gaussian Prior '
                                  '($%.3g\pm%.3g$)'%(maximum,stddev))
                plt.axvspan(
                    maximum-stddev,
                    maximum+stddev,
                    color='k',
                    label=priorlabel,
                    ymax=0.1,
                    alpha=0.5
                )
                # Reset xlimits if prior makes it go far off
                if plt.xlim()[0] < currentxlim[0]:
                    plt.xlim(currentxlim[0],plt.xlim()[1])
                if plt.xlim()[1] > currentxlim[1]:
                    plt.xlim(plt.xlim()[0],currentxlim[1])

    # Make axis labels look nice
    if systkey == 'metric_val':
        systname = make_pretty(metric_type)
    else:
        systname = make_pretty(systkey)
    if not units == 'dimensionless':
        systname += r' (%s)'%make_pretty(units)
                
    plt.xlabel(systname)
    if subplotnum is not None:
        if (subplotnum-1)%4 == 0:
            plt.ylabel(r'Number of Trials')
    else:
        plt.ylabel(r'Number of Trials')
    plt.ylim(0,1.35*plt.ylim()[1])
    if not systkey == 'metric_val':
        plt.legend(loc='upper left')
    

def plot_individual_posteriors(data, labels, all_params, detector,
                               selection, outdir):
    '''
    This function will make use of plot_individual_posterior and save every time.
    '''

    outdir = os.path.join(outdir,'IndividualPosteriors')
    if not os.path.exists(outdir):
        logging.info('Making output directory %s'%outdir)
        os.makedirs(outdir)

    MainTitle = '%s %s Event Selection Posterior'%(detector, selection)

    if labels['data_name'] == labels['h0_name']:
        inj = 'h0'
        alt = 'h1'
    else:
        inj = 'h1'
        alt = 'h0'
    injparams = fid_data[
        ('%s_fit_to_toy_%s_asimov'
         %(inj,labels['data_name']))
    ]['params']
    altparams = fid_data[
        ('%s_fit_to_toy_%s_asimov'
         %(alt,labels['data_name']))
    ]['params']
    injlabel = 'Injected Value'
    altlabel = 'Alternate Fit'

    for fhkey in data.keys():
        for systkey in data[fhkey].keys():

            hypo = fhkey.split('_')[0]
            fid = fhkey.split('_')[-2]
            FitTitle = ("True %s, Fiducial Fit %s, Hypothesis %s (%i Trials)"
                        %(labels['data_name'],
                          labels['%s_name'%fid],
                          labels['%s_name'%hypo],
                          len(data[fhkey][systkey]['vals'])))

            plot_individual_posterior(
                data = data[fhkey][systkey],
                injparams = injparams,
                altparams = altparams,
                all_params = all_params,
                labels = labels,
                injlabel = injlabel,
                altlabel = altlabel,
                systkey = systkey,
                fhkey = fhkey
            )

            plt.title(MainTitle+r'\\'+FitTitle, fontsize=16)
            SaveName = ("true_%s_%s_%s_fid_%s_hypo_%s_%s_posterior.png"
                        %(labels['data_name'],
                          detector,
                          selection,
                          labels['%s_name'%fid],
                          labels['%s_name'%hypo],
                          systkey))
            plt.savefig(os.path.join(outdir,SaveName))
            plt.close()


def plot_combined_posteriors(data, fid_data, labels, all_params,
                             detector, selection, outdir):
    '''
    This function will make use of plot_individual_posterior but just save
    once all of the posteriors for a given combination of h0 and h1 have
    been plotted on the same canvas.
    '''

    outdir = os.path.join(outdir,'CombinedPosteriors')
    if not os.path.exists(outdir):
        logging.info('Making output directory %s'%outdir)
        os.makedirs(outdir)

    MainTitle = '%s %s Event Selection Posteriors'%(detector, selection)

    labels['MainTitle'] = MainTitle

    if labels['data_name'] == labels['h0_name']:
        inj = 'h0'
        alt = 'h1'
    else:
        inj = 'h1'
        alt = 'h0'
    injparams = fid_data[
        ('%s_fit_to_toy_%s_asimov'
         %(inj,labels['data_name']))
    ]['params']
    altparams = fid_data[
        ('%s_fit_to_toy_%s_asimov'
         %(alt,labels['data_name']))
    ]['params']
    injlabel = 'Injected Value'
    altlabel = 'Alternate Fit'

    for fhkey in data.keys():
        
        # Set up multi-plot
        num_rows = get_num_rows(data[fhkey], omit_metric=False)
        plt.figure(figsize=(20,5*num_rows+2))
        subplotnum=1
        
        for systkey in data[fhkey].keys():

            hypo = fhkey.split('_')[0]
            fid = fhkey.split('_')[-2]
            FitTitle = ("True %s, Fiducial Fit %s, Hypothesis %s (%i Trials)"
                        %(labels['data_name'],
                          labels['%s_name'%fid],
                          labels['%s_name'%hypo],
                          len(data[fhkey][systkey]['vals'])))

            plt.subplot(num_rows,4,subplotnum)

            plot_individual_posterior(
                data = data[fhkey][systkey],
                injparams = injparams,
                altparams = altparams,
                all_params = all_params,
                labels = labels,
                injlabel = injlabel,
                altlabel = altlabel,
                systkey = systkey,
                fhkey = fhkey,
                subplotnum = subplotnum
            )

            subplotnum += 1

        plt.suptitle(MainTitle+r'\\'+FitTitle, fontsize=36)
        plt.tight_layout()
        plt.subplots_adjust(top=0.9)
        SaveName = ("true_%s_%s_%s_fid_%s_hypo_%s_posteriors.png"
                    %(labels['data_name'],
                      detector,
                      selection,
                      labels['%s_name'%fid],
                      labels['%s_name'%hypo]))
        plt.savefig(os.path.join(outdir,SaveName))
        plt.close()


def plot_individual_scatter(xdata, ydata, labels, xsystkey, ysystkey,
                            subplotnum=None, num_rows=None, plot_cor=True):
    '''
    This function will use matplotlib to make a scatter plot of the vals
    contained in xdata and ydata. The correlation will be calculated and
    the plot will be annotated with this. Axis labels are done in here too. The 
    optional subplotnum argument can be given in the combined case so that the 
    y-axis label only get put on when appropriate.
    '''

    # Extract data and units
    xvals = np.array(xdata['vals'])
    xunits = xdata['units']
    yvals = np.array(ydata['vals'])
    yunits = ydata['units']

    # Make scatter plot
    plt.scatter(xvals, yvals)

    if plot_cor:
        # Calculate correlation and annotate
        if len(set(xvals)) == 1:
            logging.warn(("Parameter %s appears to not have been varied. i.e. all "
                          "of the values in the set are the same. This will "
                          "lead to NaN in the correlation calculation and so it "
                          "will not be done."%xsystkey))
        if len(set(yvals)) == 1:
            logging.warn(("Parameter %s appears to not have been varied. i.e. all "
                          "of the values in the set are the same. This will "
                          "lead to NaN in the correlation calculation and so it "
                          "will not be done."%ysystkey))
        if (len(set(xvals)) != 1) and (len(set(yvals)) != 1):
            rho, pval = spearmanr(xvals, yvals)
            if subplotnum is not None:
                row = int((subplotnum-1)/4)
                xtext = 0.25*0.25+((subplotnum-1)%4)*0.25
                ytext = 0.88-(1.0/num_rows)*0.9*row
                plt.figtext(
                    xtext,
                    ytext,
                    'Correlation = %.2f'%rho,
                    fontsize='large'
                )
            else:
                plt.figtext(
                    0.15,
                    0.85,
                    'Correlation = %.2f'%rho,
                    fontsize='large'
                )

    # Make plot range easy to look at
    Xrange = xvals.max() - xvals.min()
    Yrange = yvals.max() - yvals.min()
    if Xrange != 0.0:
        plt.xlim(xvals.min()-0.1*Xrange,
                 xvals.max()+0.1*Xrange)
    if Yrange != 0.0:
        plt.ylim(yvals.min()-0.1*Yrange,
                 yvals.max()+0.3*Yrange)
    
    # Make axis labels look nice
    xsystname = make_pretty(xsystkey)
    if not xunits == 'dimensionless':
        xsystname += r' (%s)'%make_pretty(xunits)
    ysystname = make_pretty(ysystkey)
    if not yunits == 'dimensionless':
        ysystname += r' (%s)'%make_pretty(yunits)

    plt.xlabel(xsystname)
    plt.ylabel(ysystname)
    
    
def plot_individual_scatters(data, labels, detector, selection, outdir):
    '''
    This function will make use of plot_individual_scatter and save every time.
    '''

    outdir = os.path.join(outdir,'IndividualScatterPlots')
    if not os.path.exists(outdir):
        logging.info('Making output directory %s'%outdir)
        os.makedirs(outdir)

    MainTitle = '%s %s Event Selection Correlation Plot'%(detector, selection)

    for fhkey in data.keys():
        for xsystkey in data[fhkey].keys():
            if not xsystkey == 'metric_val':
                for ysystkey in data[fhkey].keys():
                    if (ysystkey != 'metric_val') and (ysystkey != xsystkey):

                        hypo = fhkey.split('_')[0]
                        fid = fhkey.split('_')[-2]
                        FitTitle = ("True %s, Fiducial Fit %s, Hypothesis %s "
                                    "(%i Trials)"
                                    %(labels['data_name'],
                                      labels['%s_name'%fid],
                                      labels['%s_name'%hypo],
                                      len(data[fhkey][xsystkey]['vals'])))

                        plot_individual_scatter(
                            xdata = data[fhkey][xsystkey],
                            ydata = data[fhkey][ysystkey],
                            labels = labels,
                            xsystkey = xsystkey,
                            ysystkey = ysystkey
                        )

                        plt.title(MainTitle+r'\\'+FitTitle, fontsize=36)
                        SaveName = (("true_%s_%s_%s_fid_%s_hypo_%s_%s_%s"
                                     "_scatter_plot.png"
                                     %(labels['data_name'],
                                      detector,
                                      selection,
                                      labels['%s_name'%fid],
                                      labels['%s_name'%hypo],
                                      xsystkey,
                                      ysystkey)))
                        plt.savefig(os.path.join(outdir,SaveName))
                        plt.close()


def plot_combined_individual_scatters(data, labels, detector,
                                      selection, outdir):
    '''
    This function will make use of plot_individual_scatter and save once all of 
    the scatter plots for a single systematic with every other systematic have 
    been plotted on the same canvas for each h0 and h1 combination.
    '''

    outdir = os.path.join(outdir,'CombinedScatterPlots')
    if not os.path.exists(outdir):
        logging.info('Making output directory %s'%outdir)
        os.makedirs(outdir)

    MainTitle = '%s %s Event Selection Correlation Plot'%(detector, selection)

    for fhkey in data.keys():
        for xsystkey in data[fhkey].keys():
            if not xsystkey == 'metric_val':
                
                # Set up multi-plot
                num_rows = get_num_rows(data[fhkey], omit_metric=True)
                plt.figure(figsize=(20,5*num_rows+2))
                subplotnum=1
                
                for ysystkey in data[fhkey].keys():
                    if (ysystkey != 'metric_val') and (ysystkey != xsystkey):

                        hypo = fhkey.split('_')[0]
                        fid = fhkey.split('_')[-2]
                        FitTitle = ("True %s, Fiducial Fit %s, Hypothesis %s "
                                    "(%i Trials)"
                                    %(labels['data_name'],
                                      labels['%s_name'%fid],
                                      labels['%s_name'%hypo],
                                      len(data[fhkey][xsystkey]['vals'])))

                        plt.subplot(num_rows,4,subplotnum)

                        plot_individual_scatter(
                            xdata = data[fhkey][xsystkey],
                            ydata = data[fhkey][ysystkey],
                            labels = labels,
                            xsystkey = xsystkey,
                            ysystkey = ysystkey,
                            subplotnum = subplotnum,
                            num_rows = num_rows
                        )

                        subplotnum += 1

                plt.suptitle(MainTitle+r'\\'+FitTitle, fontsize=36)
                plt.tight_layout()
                plt.subplots_adjust(top=0.9)
                SaveName = (("true_%s_%s_%s_fid_%s_hypo_%s_%s"
                             "_scatter_plot.png"
                             %(labels['data_name'],
                               detector,
                               selection,
                               labels['%s_name'%fid],
                               labels['%s_name'%hypo],
                               xsystkey)))
                plt.savefig(os.path.join(outdir,SaveName))
                plt.close()


def plot_combined_scatters(data, labels, detector, selection, outdir):
    '''
    This function will make use of plot_individual_scatter and save once every 
    scatter plot has been plotted on a single canvas for each of the h0 and h1 
    combinations.
    '''

    outdir = os.path.join(outdir,'CombinedScatterPlots')
    if not os.path.exists(outdir):
        logging.info('Making output directory %s'%outdir)
        os.makedirs(outdir)

    MainTitle = '%s %s Event Selection Correlation Plot'%(detector, selection)

    for fhkey in data.keys():
        # Systematic number is one less than number of keys since this also
        # contains the metric_val entry
        SystNum = len(data[fhkey].keys())-1
        # Set up multi-plot
        plt.figure(figsize=(3.5*(SystNum-1),3.5*(SystNum-1)))
        subplotnum=(SystNum-1)*(SystNum-1)+1
        # Set up container to know which correlations have already been plotted
        PlottedSysts = []
        for xsystkey in data[fhkey].keys():
            if not xsystkey == 'metric_val':
                PlottedSysts.append(xsystkey)
                for ysystkey in data[fhkey].keys():
                    if (ysystkey != 'metric_val') and (ysystkey != xsystkey):
                        subplotnum -= 1
                        if ysystkey not in PlottedSysts:

                            hypo = fhkey.split('_')[0]
                            fid = fhkey.split('_')[-2]
                            FitTitle = ("True %s, Fiducial Fit %s, Hypothesis "
                                        "%s (%i Trials)"
                                        %(labels['data_name'],
                                          labels['%s_name'%fid],
                                          labels['%s_name'%hypo],
                                          len(data[fhkey][xsystkey]['vals'])))
                            
                            plt.subplot(SystNum-1,SystNum-1,subplotnum)

                            plot_individual_scatter(
                                xdata = data[fhkey][xsystkey],
                                ydata = data[fhkey][ysystkey],
                                labels = labels,
                                xsystkey = xsystkey,
                                ysystkey = ysystkey,
                                plot_cor = False
                            )

        plt.suptitle(MainTitle+r'\\'+FitTitle, fontsize=120)
        plt.tight_layout()
        plt.subplots_adjust(top=0.9)
        SaveName = (("true_%s_%s_%s_fid_%s_hypo_%s_all"
                     "_scatter_plots.png"
                     %(labels['data_name'],
                       detector,
                       selection,
                       labels['%s_name'%fid],
                       labels['%s_name'%hypo])))
        plt.savefig(os.path.join(outdir,SaveName))
        plt.close()


def plot_correlation_matrices(data, labels, detector, selection, outdir):
    '''
    This will plot the correlation matrices since the individual scatter plots 
    are a pain to interpret on their own. This will plot them with a colour 
    scale and, if the user has the PathEffects module then it will also write 
    the values on the bins. If a number is invalid it will come up bright green.
    '''
    try:
        import matplotlib.patheffects as PathEffects
        logging.warn("PathEffects could be imported, so the correlation values"
                     " will be written on the bins. This is slow.")
        pe = True
    except:
        logging.warn("PathEffects could not be imported, so the correlation" 
                     " values will not be written on the bins.")
        pe = False

    outdir = os.path.join(outdir,'CorrelationMatrices')
    if not os.path.exists(outdir):
        logging.info('Making output directory %s'%outdir)
        os.makedirs(outdir)

    MainTitle = ("%s %s Event Selection Correlation Coefficients"
                 %(detector, selection))
    Systs = []

    for fhkey in data.keys():
        # Systematic number is one less than number of keys since this also
        # contains the metric_val entry
        SystNum = len(data[fhkey].keys())-1
        # Set up array to hold lists of correlation values
        all_corr_lists = []
        for xsystkey in data[fhkey].keys():
            all_corr_values = []
            if not xsystkey == 'metric_val':
                if make_pretty(xsystkey) not in Systs:
                    Systs.append(make_pretty(xsystkey))
                for ysystkey in data[fhkey].keys():
                    if (ysystkey != 'metric_val'):
                        hypo = fhkey.split('_')[0]
                        fid = fhkey.split('_')[-2]
                        FitTitle = ("True %s, Fiducial Fit %s, Hypothesis "
                                    "%s (%i Trials)"
                                    %(labels['data_name'],
                                      labels['%s_name'%fid],
                                      labels['%s_name'%hypo],
                                      len(data[fhkey][xsystkey]['vals'])))

                        # Calculate correlation
                        xvals = np.array(data[fhkey][xsystkey]['vals'])
                        yvals = np.array(data[fhkey][ysystkey]['vals'])
                        if len(set(xvals)) == 1:
                            logging.warn(("Parameter %s appears to not have "
                                          "been varied. i.e. all of the values"
                                          " in the set are the same. This will"
                                          " lead to NaN in the correlation "
                                          "calculation and so it will not be "
                                          "done."%xsystkey))
                        if len(set(yvals)) == 1:
                            logging.warn(("Parameter %s appears to not have "
                                          "been varied. i.e. all of the values"
                                          " in the set are the same. This will"
                                          " lead to NaN in the correlation "
                                          "calculation and so it will not be "
                                          "done."%ysystkey))
                        if (len(set(xvals)) != 1) and (len(set(yvals)) != 1):
                            rho, pval = spearmanr(xvals, yvals)
                        else:
                            rho = np.nan
                        all_corr_values.append(rho)
                all_corr_lists.append(all_corr_values)

        all_corr_nparray = np.ma.masked_invalid(np.array(all_corr_lists))
        # Plot it!
        palette = plt.cm.RdBu
        palette.set_bad('lime',1.0)
        plt.imshow(
            all_corr_nparray,
            interpolation='none',
            cmap=plt.cm.RdBu,
            vmin=-1.0,
            vmax=1.0
        )
        plt.colorbar()
        # Add systematic names as x and y axis ticks
        plt.xticks(
            np.arange(len(Systs)),
            Systs,
            rotation=45,
            horizontalalignment='right'
        )
        plt.yticks(
            np.arange(len(Systs)),
            Systs,
            rotation=0
        )
        plt.tight_layout()
        plt.subplots_adjust(bottom=0.30,left=-0.30,right=1.05,top=0.9)
        plt.title(MainTitle+r'\\'+FitTitle, fontsize=16)
        SaveName = (("true_%s_%s_%s_fid_%s_hypo_%s_correlation_matrix.png"
                     %(labels['data_name'],
                       detector,
                       selection,
                       labels['%s_name'%fid],
                       labels['%s_name'%hypo])))
        plt.savefig(os.path.join(outdir,SaveName))
        if pe:
            for i in range(0,len(all_corr_nparray)):
                for j in range(0,len(all_corr_nparray[0])):
                    plt.text(i, j, '%.2f'%all_corr_nparray[i][j],
                             fontsize='7',
                             verticalalignment='center',
                             horizontalalignment='center',
                             color='w',
                             path_effects=[
                                 PathEffects.withStroke(
                                     linewidth=2.5,
                                     foreground='k'
                                 )
                             ])
        SaveName = (("true_%s_%s_%s_fid_%s_hypo_%s_correlation_matrix_"
                     "values.png"
                     %(labels['data_name'],
                       detector,
                       selection,
                       labels['%s_name'%fid],
                       labels['%s_name'%hypo])))
        plt.savefig(os.path.join(outdir,SaveName))
        plt.close()

    
def parse_args():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        '-d', '--dir', required=True,
        metavar='DIR', type=str,
        help='Directory into which to store results and metadata.'
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        '--asimov', action='store_true',
        help='''Analyze the Asimov trials in the specified directories.'''
    )
    group.add_argument(
        '--llr', action='store_true',
        help='''Analyze the LLR trials in the specified directories.'''
    )
    parser.add_argument(
        '--detector',type=str,default='',
        help="Name of detector to put in histogram titles."
    )
    parser.add_argument(
        '--selection',type=str,default='',
        help="Name of selection to put in histogram titles."
    )
    parser.add_argument(
        '-IP','--individual_posteriors',action='store_true',default=False,
        help="Flag to plot individual posteriors."
    )
    parser.add_argument(
        '-CP','--combined_posteriors',action='store_true',default=False,
        help="Flag to plot combined posteriors for each h0 and h1 combination."
    )
    parser.add_argument(
        '-IS','--individual_scatter',action='store_true',default=False,
        help="Flag to plot individual 2D scatter plots of posteriors."
    )
    parser.add_argument(
        '-CIS','--combined_individual_scatter',
        action='store_true',default=False,
        help="""Flag to plot all 2D scatter plots of one systematic with every 
        other systematic on one plot for each h0 and h1 combination."""
    )
    parser.add_argument(
        '-CS','--combined_scatter', action='store_true',default=False,
        help="""Flag to plot all 2D scatter plots on one plot for each 
        h0 and h1 combination."""
    )
    parser.add_argument(
        '-CM', '--correlation_matrix', action='store_true',default=False,
        help="""Flag to plot the correlation matrices for each h0 and h1 
        combination."""
    )
    parser.add_argument(
        '--outdir', metavar='DIR', type=str, required=True,
        help="""Store all output plots to this directory. This will make
        further subdirectories, if needed, to organise the output plots."""
    )
    parser.add_argument(
        '-v', action='count', default=None,
        help='set verbosity level'
    )
    return parser.parse_args()


def main():
    args = parse_args()
    init_args_d = vars(args)

    # NOTE: Removing extraneous args that won't get passed to instantiate the
    # HypoTesting object via dictionary's `pop()` method.

    set_verbosity(init_args_d.pop('v'))

    detector = init_args_d.pop('detector')
    selection = init_args_d.pop('selection')
    iposteriors = init_args_d.pop('individual_posteriors')
    cposteriors = init_args_d.pop('combined_posteriors')
    iscatter = init_args_d.pop('individual_scatter')
    ciscatter = init_args_d.pop('combined_individual_scatter')
    cscatter = init_args_d.pop('combined_scatter')
    cmatrix = init_args_d.pop('correlation_matrix')
    outdir = init_args_d.pop('outdir')

    if args.asimov:
        data_sets, all_params, labels = extract_trials(
            logdir=args.dir,
            fluctuate_fid=False,
            fluctuate_data=False
        )
        od = data_sets.values()[0]
        #if od['h1_fit_to_h0_fid']['fid_asimov']['metric_val'] > od['h0_fit_to_h1_fid']['fid_asimov']['metric_val']:
        print np.sqrt(np.abs(od['h1_fit_to_h0_fid']['fid_asimov']['metric_val'] - od['h0_fit_to_h1_fid']['fid_asimov']['metric_val']))

    else:
        data_sets, all_params, labels = extract_trials(
            logdir=args.dir,
            fluctuate_fid=True,
            fluctuate_data=False
        )
        
        fid_values = extract_fid_data(data_sets)
        values = extract_data(data_sets)

        for injkey in values.keys():

            '''
            make_llr_plots(
                data = values[injkey],
                labels = labels.dict,
                detector = detector,
                selection = selection,
                outdir = outdir
            )
            '''

            if iposteriors:

                plot_individual_posteriors(
                    data = values[injkey],
                    fid_data = fid_values[injkey],
                    labels = labels.dict,
                    all_params = all_params,
                    detector = detector,
                    selection = selection,
                    outdir = outdir
                )

            if cposteriors:

                plot_combined_posteriors(
                    data = values[injkey],
                    fid_data = fid_values[injkey],
                    labels = labels.dict,
                    all_params = all_params,
                    detector = detector,
                    selection = selection,
                    outdir = outdir
                )

            if iscatter:

                plot_individual_scatters(
                    data = values[injkey],
                    labels = labels.dict,
                    detector = detector,
                    selection = selection,
                    outdir = outdir
                )

            if ciscatter:

                plot_combined_individual_scatters(
                    data = values[injkey],
                    labels = labels.dict,
                    detector = detector,
                    selection = selection,
                    outdir = outdir
                )

            if cscatter:

                plot_combined_scatters(
                    data = values[injkey],
                    labels = labels.dict,
                    detector = detector,
                    selection = selection,
                    outdir = outdir
                )

            if cmatrix:

                plot_correlation_matrices(
                    data = values[injkey],
                    labels = labels.dict,
                    detector = detector,
                    selection = selection,
                    outdir = outdir
                )
                
        
if __name__ == '__main__':
    main()
