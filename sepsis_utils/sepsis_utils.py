from __future__ import print_function

import sys
import os
import psycopg2
import pandas as pd
import numpy as np

# we use ordered dictionaries to ensure consistent output order
import collections

import roc_utils as ru

from statsmodels.formula.api import logit

from sklearn import metrics
import scipy.stats

# create a database connection

# below config used on pc70
sqluser = 'alistairewj'
dbname = 'mimic'
schema_name = 'mimiciii'

def get_data(exclusions=None):
    # this function connects to a local database (details above) and pulls data from the
    # sepsis3 table, generated by queries in the `query` subfolder
    # if exclusions are desired, they should be input as a string to the function
    # these are used after the WHERE clause in the SQL query
    # e.g. exclusions='adult = 1 and icustay_num = 1' would add
    #   'where adult = 1 and icustay_num = 1' to the end of the query

    if exclusions is None:
        query = 'select * from ' + schema_name + '.sepsis3 order by icustay_id'
    else:
        query = 'select * from ' + schema_name + '.sepsis3 ' \
        + 'where ' + exclusions + ' order by icustay_id'

    try:
        # Connect to local postgres version of mimic
        con = psycopg2.connect(dbname=dbname, user=sqluser)
        df = pd.read_sql_query(query,con)

    except psycopg2.OperationalError, exception:
        # failed to log in
        print('Error when trying to connect to the database!')
        raise exception

    except pd.io.sql.DatabaseError, exception:
        # failed to query the data
        if 'relation "mimiciii.sepsis3" does not exist' in exception.args[0]:
            print('Could not find the sepsis3 view - did you run the SQL scripts in the query subfolder?')
        raise exception

    finally:
        if con:
            con.close()

    # cast datatypes appropriately
    df['suspected_infection_time'] = pd.to_datetime(df['suspected_infection_time'])
    df['intime'] = pd.to_datetime(df['intime'])
    df['outtime'] = pd.to_datetime(df['outtime'])

    # add in various covariates based on those extracted
    df['race_black'] = np.in1d(df['ethnicity'],
                               ('BLACK/AFRICAN AMERICAN','BLACK/CAPE VERDEAN','BLACK/HAITIAN','BLACK/AFRICAN'))
    df['race_other'] = \
    (np.in1d(df['ethnicity'],('BLACK/AFRICAN AMERICAN','BLACK/CAPE VERDEAN','BLACK/HAITIAN','BLACK/AFRICAN'))==0) \
    & (np.in1d(df['ethnicity'],('WHITE','WHITE - RUSSIAN','WHITE - OTHER EUROPEAN','WHITE - BRAZILIAN','WHITE - EASTERN EUROPEAN'))==0)

    df['is_male'] = np.in1d(df['gender'],('M'))

    return df

def print_cm(y, yhat, header1='y', header2='yhat'):
    print('\nConfusion matrix')
    cm = metrics.confusion_matrix(y, yhat)
    TN = cm[0,0]
    FP = cm[0,1]
    FN = cm[1,0]
    TP = cm[1,1]
    N = TN+FP+FN+TP
    print('      \t{:6s}\t{:6s}'.format(header1 + '=0', header1 + '=1'))
    print('{:6s}\t{:6g}\t{:6g}\tNPV={:2.2f}'.format(header2 + '=0', cm[0,0],cm[1,0], 100.0*TN / (TN+FN))) # NPV
    print('{:6s}\t{:6g}\t{:6g}\tPPV={:2.2f}'.format(header2 + '=1', cm[0,1],cm[1,1], 100.0*TP / (TP+FP))) # PPV
    # add sensitivity/specificity as the bottom line
    print('   \t{:2.2f}\t{:2.2f}\tAcc={:2.2f}'.format(100.0*TN/(TN+FP), 100.0*TP/(TP+FN), 100.0*(TP+TN)/N))
    print('   \tSpec\tSens')

def get_op_stats(yhat_all, y_all, yhat_names=None, header=None, idx=None):
    # for a given set of predictions, prints a table of the performances
    # yhat_all should be an 1xM list containing M numpy arrays of length N
    # y_all is either an Nx1 numpy array (if evaluating against the same outcome)
    # ... or it's an 1xM list containing M numpy arrays of length N

    if 'numpy' in str(type(y_all)):
        # targets input as a single array
        # we create a 1xM list the same size as yhat_all
        y_all = [y_all for i in range(len(yhat_all))]

    stats_names = [ 'TN','FP','FN','TP','Sens','Spec','PPV','NPV','F1','NTP','NFP']
    stats_all = np.zeros( [len(yhat_all), len(stats_names)] )
    ci = np.zeros( [len(yhat_all), len(stats_names), 2] )

    TN = np.zeros(len(yhat_all))
    FP = np.zeros(len(yhat_all))
    FN = np.zeros(len(yhat_all))
    TP = np.zeros(len(yhat_all))

    for i, yhat in enumerate(yhat_all):
        if idx is not None:
            cm = metrics.confusion_matrix(y_all[i][idx[i]], yhat[idx[i]])
        else:
            cm = metrics.confusion_matrix(y_all[i], yhat)

        # confusion matrix is output as int64 - we'd like to calculate percentages
        cm = cm.astype(float)
        # to make the code clearer, extract components from confusion matrix
        TN = cm[0,0] # true negatives
        FP = cm[0,1] # false positives
        FN = cm[1,0] # false negatives
        TP = cm[1,1] # true positives

        stats_all[i,4] = 100.0*TP/(TP+FN) # Sensitivity
        stats_all[i,5] = 100.0*TN/(TN+FP) # Specificity
        stats_all[i,6] = 100.0*TP/(TP+FP) # PPV
        stats_all[i,7] = 100.0*TN/(TN+FN) # NPV

        # F1, the harmonic mean of PPV/Sensitivity
        stats_all[i,8] = 2.0*(stats_all[i,6] * stats_all[i,4]) / (stats_all[i,6] + stats_all[i,4])

        # NTP/100: 100 patients * % outcome * (ppv)
        stats_all[i,9] = 100.0 * (TP+FP)/(TP+FP+TN+FN) * (stats_all[i,6]/100.0)
        # NFP/100: 100 patients * % outcome * (1-ppv)
        stats_all[i,10] = 100.0 * (TP+FP)/(TP+FP+TN+FN) * (1-stats_all[i,6]/100.0)

        #stats_all[i,11] = (TP/FP)/(FN/TN) # diagnostic odds ratio

        # now push the stats to the final stats vector
        stats_all[i,0] = TN
        stats_all[i,1] = FP
        stats_all[i,2] = FN
        stats_all[i,3] = TP

    return stats_all


def print_op_stats(stats_all, yhat_names=None, header=None, idx=None):
    stats_names = [ 'TN','FP','FN','TP','Sens','Spec','PPV','NPV','F1','NTP','NFP']
    # calculate confidence intervals
    P = stats_all.shape[0]
    ci = np.zeros( [P, len(stats_names), 2] )

    for i in range(P):
        TN = stats_all[i,0]
        FP = stats_all[i,1]
        FN = stats_all[i,2]
        TP = stats_all[i,3]

        # add the CI
        ci[i,4,:] = binomial_proportion_ci(TP, TP+FN, alpha = 0.05)
        ci[i,5,:] = binomial_proportion_ci(TN, TN+FP, alpha = 0.05)
        ci[i,6,:] = binomial_proportion_ci(TP, TP+FP, alpha = 0.05)
        ci[i,7,:] = binomial_proportion_ci(TN, TN+FN, alpha = 0.05)

    print('Metric')
    if header is not None:
        if type(header)==str:
            print(''.format(header))
        else:
            for i, hdr_name in enumerate(header):
                print('\t{:5s}'.format(hdr_name), end='')
            print('') # newline

    # print the names of the predictions, if they were provided
    print('') # newline
    print('{:5s}'.format(''),end='') # spacing
    if yhat_names is not None:
        for i, yhat_name in enumerate(yhat_names):
            print('\t{:20s}'.format(yhat_name), end='')
        print('') # newline

    # print the stats calculated
    for n, stats_name in enumerate(stats_names):
        print('{:5s}'.format(stats_name), end='')
        for i, yhat_name in enumerate(yhat_names):
            if n < 4: # use integer format for the tp/fp
                print('\t{:5.0f} {:10s}'.format(stats_all[i,n], ''), end='')
            elif n < 8: # print sensitivity, specificity, etc with CI
                print('\t{:2.2f} [{:2.2f}, {:2.2f}]'.format(stats_all[i,n],
                ci[i,n,0],ci[i,n,1]),end='')
            else: # use decimal format for the rest
                print('\t{:2.2f} {:12s}'.format(stats_all[i,n], ''), end='')

        print('') # newline

    return None

def print_stats_to_file(filename, yhat_names, stats_all):
    # print the table to a file for convenient viewing
    f = open(filename,'w')
    stats_names = [ 'TN','FP','FN','TP','N','Sens','Spec','PPV','NPV','F1','NTP','NFP']

    # derive CIs
    ci = np.zeros( [stats_all.shape[0], stats_all.shape[1], 2] )
    for i in range(stats_all.shape[0]):
        # add the CI
        TN = stats_all[i,0]
        FP = stats_all[i,1]
        FN = stats_all[i,2]
        TP = stats_all[i,3]

        ci[i,4,:] = binomial_proportion_ci(TP, TP+FN, alpha = 0.05)
        ci[i,5,:] = binomial_proportion_ci(TN, TN+FP, alpha = 0.05)
        ci[i,6,:] = binomial_proportion_ci(TP, TP+FP, alpha = 0.05)
        ci[i,7,:] = binomial_proportion_ci(TN, TN+FN, alpha = 0.05)

    f.write('Subgroup')
    for n, stats_name in enumerate(stats_names):
        f.write('\t%s' % stats_name)

    f.write('\n')

    for i, yhat_name in enumerate(yhat_names):
        f.write('%s' % yhat_name)

        for n, stats_name in enumerate(stats_names):
            if n < 5: # use integer format for the tp/fp
                f.write('\t%10.0f' % stats_all[i,n])
            elif n < 8: # print sensitivity, specificity, etc with CI
                f.write('\t%4.2f [{:2.2f}, {:2.2f}]' % stats_all[i,n], ci[i,n,0], ci[i,n,1])
            else: # use decimal format for the sensitivity, specificity, etc
                f.write('\t%10.2f' % stats_all[i,n])

        f.write('\n') # newline

    f.close()

def print_demographics(df, idx=None):
    # create a dictionary which maps each variable to a data type
    all_vars = collections.OrderedDict((
    ('N', 'N'),
    ('age', 'median'),
    ('gender', 'gender'), # handled specially
    ('bmi', 'continuous'),
    ('hospital_expire_flag', 'binary'),
    ('thirtyday_expire_flag', 'binary'),
    ('icu_los', 'median'),
    ('hosp_los', 'median'),
    ('vent', 'binary'),
    ('race', 'race'),
    ('elixhauser_hospital', 'median'),
    ('sirs', 'median'),
    ('sofa', 'median'),
    ('qsofa', 'median'),
    ('mlods', 'median'),
    ('lactate_max', 'continuous')))

    if idx is None:
        # print demographics for entire dataset
        for i, curr_var in enumerate(all_vars):
            if all_vars[curr_var] == 'N': # print number of patients
                print('{:20s}\t{:4g}'.format(curr_var, df.shape[0]))
            elif curr_var in df.columns:
                if all_vars[curr_var] == 'continuous': # report mean +- STD
                    print('{:20s}\t{:2.2f} +- {:2.2f}'.format(curr_var, df[curr_var].mean(), df[curr_var].std()))
                elif all_vars[curr_var] == 'gender': # convert from M/F
                    print('{:20s}\t{:4g} ({:2.2f}%)'.format(curr_var, np.sum(df[curr_var].values=='M'),
                    100.0*np.sum(df[curr_var].values=='M').astype(float) / df.shape[0]))
                # binary, report percentage
                elif all_vars[curr_var] == 'binary':
                    print('{:20s}\t{:4g} ({:2.2f}%)'.format(curr_var, df[curr_var].sum(),
                    100.0*(df[curr_var].mean()).astype(float)))
                # report median [25th percentile, 75th percentile]
                elif all_vars[curr_var] == 'median':
                    print('{:20s}\t{:2.2f} [{:2.2f}, {:2.2f}]'.format(curr_var, df[curr_var].median(),
                    np.percentile(df[curr_var].values,25,interpolation='midpoint'), np.percentile(df[curr_var].values,75,interpolation='midpoint')))
                elif all_vars[curr_var] == 'measured':
                    print('{:20s}\t{:2.2f}%'.format(curr_var, 100.0*np.mean(df[curr_var].isnull())))
                elif all_vars[curr_var] == 'race':
                    # special case: print each race individually
                    # race_black, race_other
                    print('{:20s}\t'.format('Race'))

                    # each component
                    curr_var_tmp = 'White'
                    N_tmp = df.shape[0]-(df['race_black'].sum()+df['race_other'].sum())
                    print('{:20s}\t{:4g} ({:2.2f}%)'.format(curr_var_tmp, N_tmp,
                    100.0*(N_tmp.astype(float))/df.shape[0]))
                    curr_var_tmp = 'Black'
                    print('{:20s}\t{:4g} ({:2.2f}%)'.format(curr_var_tmp, df['race_black'].sum(),
                    100.0*(df['race_black'].mean()).astype(float)))
                    curr_var_tmp = 'Other'
                    print('{:20s}\t{:4g} ({:2.2f}%)'.format(curr_var_tmp, df['race_other'].sum(),
                    100.0*(df['race_other'].mean()).astype(float)))

                # additional lactate measurements output with lactate_max
                if curr_var == 'lactate_max':
                    # also print measured
                    print('{:20s}\t{:4g} ({:2.2f}%)'.format(curr_var.replace('_max',' ') + 'measured',
                    np.sum(df[curr_var].isnull()),100.0*np.mean(df[curr_var].isnull())))
                    print('{:20s}\t{:4g} ({:2.2f}%)'.format(curr_var.replace('_max',' ') + '> 2',
                    np.sum(df[curr_var] >= 2),100.0*np.mean(df[curr_var] >= 2)))

            else:
                print('{:20s}'.format(curr_var))
    else:
        # print demographics split into two groups
        # also print p-values testing between the two groups
        for i, curr_var in enumerate(all_vars):
            if all_vars[curr_var] == 'N': # print number of patients
                print('{:20s}\t{:4g}{:5s}\t{:4g}{:5s}\t{:5s}'.format(curr_var,
                np.sum(~idx), '',
                np.sum(idx), '',
                ''))
            elif curr_var in df.columns:
                if all_vars[curr_var] == 'continuous': # report mean +- STD
                    tbl = np.array([ [df[~idx][curr_var].mean(), df[idx][curr_var].mean()],
                    [df.loc[~idx,curr_var].std(), df.loc[idx,curr_var].std()]])

                    stat, pvalue = scipy.stats.ttest_ind(df[~idx][curr_var], df[idx][curr_var],
                    equal_var=False, nan_policy='omit')

                    # print out < 0.001 if it's a very low p-value
                    if pvalue < 0.001:
                        pvalue = '< 0.001'
                    else:
                        pvalue = '{:0.3f}'.format(pvalue)

                    print('{:20s}\t{:2.2f} +- {:2.2f}\t{:2.2f} +- {:2.2f}\t{:5s}'.format(curr_var,
                    tbl[0,0], tbl[1,0],
                    tbl[0,1], tbl[1,1],
                    pvalue))

                elif all_vars[curr_var] in ('gender','binary'): # convert from M/F
                    # build the contingency table
                    if all_vars[curr_var] == 'gender':
                        tbl = np.array([ [np.sum(df[~idx][curr_var].values=='M'), np.sum(df[idx][curr_var].values=='M')],
                        [np.sum(df[~idx][curr_var].values!='M'), np.sum(df[idx][curr_var].values!='M')] ])
                    else:
                        tbl = np.array([ [np.sum(df[~idx][curr_var].values), np.sum(df[idx][curr_var].values)],
                        [np.sum(1 - df[~idx][curr_var].values), np.sum(1 - df[idx][curr_var].values)] ])


                    # get the p-value
                    chi2, pvalue, dof, ex = scipy.stats.chi2_contingency( tbl )

                    # print out < 0.001 if it's a very low p-value
                    if pvalue < 0.001:
                        pvalue = '< 0.001'
                    else:
                        pvalue = '{:0.3f}'.format(pvalue)

                    # binary, report percentage
                    print('{:20s}\t{:4g} ({:2.2f}%)\t{:4g} ({:2.2f}%)\t{:5s}'.format(curr_var,
                    tbl[0,0], 100.0*tbl[0,0].astype(float) / (tbl[0,0]+tbl[1,0]),
                    tbl[0,1],
                    100.0*tbl[0,1].astype(float) / (tbl[0,1]+tbl[1,1]),
                    pvalue))

                elif all_vars[curr_var] == 'median':
                    stat, pvalue = scipy.stats.mannwhitneyu(df[~idx][curr_var],
                    df[idx][curr_var],
                    use_continuity=True, alternative='two-sided')

                    # print out < 0.001 if it's a very low p-value
                    if pvalue < 0.001:
                        pvalue = '< 0.001'
                    else:
                        pvalue = '{:0.3f}'.format(pvalue)

                    print('{:20s}\t{:2.2f} [{:2.2f}, {:2.2f}]\t{:2.2f} [{:2.2f}, {:2.2f}]\t{:5s}'.format(curr_var,
                    df[~idx][curr_var].median(), np.percentile(df[~idx][curr_var].values,25,interpolation='midpoint'), np.percentile(df[~idx][curr_var].values,75,interpolation='midpoint'),
                    df[idx][curr_var].median(), np.percentile(df[idx][curr_var].values,25,interpolation='midpoint'), np.percentile(df[idx][curr_var].values,75,interpolation='midpoint'),
                    pvalue))

                elif all_vars[curr_var] == 'measured':
                    # build the contingency table
                    tbl = np.array([ [np.sum(df[~idx][curr_var].isnull()), np.sum(df[idx][curr_var].isnull())],
                    [np.sum(~df[~idx][curr_var].isnull()), np.sum(~df[idx][curr_var].isnull())] ])

                    # get the p-value
                    chi2, pvalue, dof, ex = scipy.stats.chi2_contingency( tbl )

                    # print out < 0.001 if it's a very low p-value
                    if pvalue < 0.001:
                        pvalue = '< 0.001'
                    else:
                        pvalue = '{:0.3f}'.format(pvalue)

                    print('{:20s}\t{:2.2f}%\t{:2.2f}%'.format(curr_var,
                    np.sum(df[~idx][curr_var].isnull()),
                    100.0*np.mean(df[~idx][curr_var].isnull()),
                    np.sum(df[idx][curr_var].isnull()),
                    100.0*np.mean(df[idx][curr_var].isnull()),
                    pvalue))

                elif all_vars[curr_var] == 'race':
                    # special case: evaluate each race in chi2
                    # race_black, race_other

                    # create a contingency table with three rows

                    # method 1) use crosstab
                    #df['race'] = 'white'
                    #df.loc[df['race_black']==1,'race'] = 'black'
                    #df.loc[df['race_other']==1,'race'] = 'other'
                    #tbl = pd.crosstab(df.race, df.angus, margins = True)
                    # # Extract table without totals
                    #tbl = tbl.ix[0:-1,0:-1]

                    # method 2) do it manually!
                    tbl = np.array([ [np.sum( ~((df[~idx]['race_black'].values) | (df[~idx]['race_other'].values))),
                    np.sum(~((df[idx]['race_black'].values) | (df[idx]['race_other'].values)))],
                    [np.sum(df[~idx]['race_black'].values), np.sum(df[idx]['race_black'].values)],
                    [np.sum(df[~idx]['race_other'].values), np.sum(df[idx]['race_other'].values)] ])


                    # get the p-value
                    chi2, pvalue, dof, ex = scipy.stats.chi2_contingency( tbl, correction=False )

                    # print out < 0.001 if it's a very low p-value
                    if pvalue < 0.001:
                        pvalue = '< 0.001'
                    else:
                        pvalue = '{:0.3f}'.format(pvalue)

                    # first print out we are comparing races (with p-value)
                    print('{:20s}\t{:10s}\t{:10s}\t{:5s}'.format(curr_var,'','',pvalue))
                    # next print out individual race #s (no p-value)
                    curr_var_vec = ['White','Black','Other']
                    for r in range(3):
                        print('{:20s}\t{:4g} ({:2.2f}%)\t{:4g} ({:2.2f}%)\t{:5s}'.format('  ' + curr_var_vec[r],
                        tbl[r,0], 100.0*tbl[r,0].astype(float) / np.sum(tbl[:,0]),
                        tbl[r,1],
                        100.0*tbl[r,1].astype(float) / np.sum(tbl[:,1]),
                        '')) # no individual p-value

                # additional lactate measurements output with lactate_max
                if curr_var == 'lactate_max':
                    # for lactate, we print two additional rows:
                    # 1) was lactate ever measured?
                    # 2) was lactate ever > 2 ?

                    # measured...
                    # build the contingency table
                    tbl = np.array([ [np.sum(df[~idx][curr_var].isnull()), np.sum(df[idx][curr_var].isnull())],
                    [np.sum(~df[~idx][curr_var].isnull()), np.sum(~df[idx][curr_var].isnull())] ])

                    # get the p-value
                    chi2, pvalue, dof, ex = scipy.stats.chi2_contingency( tbl )

                    # print out < 0.001 if it's a very low p-value
                    if pvalue < 0.001:
                        pvalue = '< 0.001'
                    else:
                        pvalue = '{:0.3f}'.format(pvalue)

                    print('{:20s}\t{:4g} ({:2.2f}%)\t{:4g} ({:2.2f}%)\t{:5s}'.format(curr_var.replace('_max',' ') + 'measured',
                    np.sum(df[~idx][curr_var].isnull()),
                    100.0*np.mean(df[~idx][curr_var].isnull()),
                    np.sum(df[idx][curr_var].isnull()),
                    100.0*np.mean(df[idx][curr_var].isnull()),
                    pvalue))


                    # value > 2...
                    # build the contingency table
                    tbl = np.array([ [np.sum(df[~idx][curr_var] >= 2), np.sum(df[idx][curr_var] >= 2)],
                    [np.sum(~(df[~idx][curr_var] >= 2)), np.sum(~(df[idx][curr_var] >= 2))] ])

                    # get the p-value
                    chi2, pvalue, dof, ex = scipy.stats.chi2_contingency( tbl )

                    # print out < 0.001 if it's a very low p-value
                    if pvalue < 0.001:
                        pvalue = '< 0.001'
                    else:
                        pvalue = '{:0.3f}'.format(pvalue)

                    print('{:20s}\t{:4g} ({:2.2f}%)\t{:4g} ({:2.2f}%)\t{:5s}'.format(curr_var.replace('_max',' ') + '> 2',
                    np.sum( df[~idx][curr_var] >= 2 ),
                    100.0*np.mean(df[~idx][curr_var] >= 2),
                    np.sum( df[idx][curr_var] >= 2 ),
                    100.0*np.mean(df[idx][curr_var] >= 2),
                    pvalue))

            else:
                print('{:20s}'.format(curr_var))


def calc_predictions(df, preds_header, target_header, model=None):
    if model is None:

        preds = dict()
        for x in preds_header:
            preds[x] = df[x].values
        return preds
    elif model == 'mfp_baseline':
        # evaluate the MFP model without severity of illness
        # call a subprocess to run the R script to generate fractional polynomial predictions
        import subprocess
        # loop through each severity score, build an MFP model for each
        fn_in = "sepsis3-design-matrix.csv"
        fn_out = "sepsis3-preds.csv"

        # by excluding the 4th argument, we train a baseline MFP model
        rcmd = ["Rscript r-make-sepsis3-models.R", fn_in, fn_out, target_header]
        err = subprocess.call(' '.join(rcmd), shell=True)
        if err!=0:
            print('RScript returned error status {}.'.format(err))
        else:
            # load in the predictions
            pred = pd.read_csv(fn_out, sep=',', header=0)
            pred = pred.values[:,0]
        return pred
    elif model == 'logreg':
        P = len(preds_header)
        y = df[target_header].values == 1
        preds = dict()
        for p in range(P):
            # build the models and get the predictions
            model = logit(formula=target_header + " ~ age + elixhauser_hospital" +
            " + race_black + race_other + is_male + " + preds_header[p],
            data=df).fit(disp=0)

            # create a list, each element containing the predictions
            preds[preds_header[p]] = model.predict()

        return preds
    elif model == 'mfp':
        # call a subprocess to run the R script to generate fractional polynomial predictions
        import subprocess
        # loop through each severity score, build an MFP model for each
        fn_in = "sepsis3-design-matrix.csv"
        fn_out = "sepsis3-preds.csv"
        preds = dict()
        for p in preds_header:
            rcmd = ["Rscript r-make-sepsis3-models.R", fn_in, fn_out, target_header, p] # note 4th argument is covariate 'p'
            err = subprocess.call(' '.join(rcmd), shell=True)
            if err!=0:
                print('RScript returned error status {}.'.format(err))
            else:
                # load in the predictions
                pred = pd.read_csv(fn_out, sep=',', header=0)
                preds[p] = pred.values[:,0]
        return preds
    else:
        print('Unsure what {} means...'.format(model))
        return None


def cronbach_alpha(X):
    # given a set of with K components (K rows) of N observations (N columns)
    # we output the agreement among the components according to Cronbach's alpha
    X = np.asarray(X)
    return X.shape[0] / (X.shape[0] - 1.0) * (1.0 - (X.var(axis=1, ddof=1).sum() / X.sum(axis=0).var(ddof=1)))

def cronbach_alpha_bootstrap(X,B=1000):
    # bootstrap cronbach - return value and confidence intervals (percentile method)
    alpha = np.zeros(B,dtype=float)
    N = X.shape[1]

    for b in range(B):
        idx = np.random.randint(0, high=N, size=N)
        alpha[b] = cronbach_alpha(X[:,idx])

    ci = np.percentile(alpha, [5,95])
    alpha = cronbach_alpha(X)
    return alpha, ci

def print_auc_table(preds, target, preds_header):
    # prints a table of AUROCs and p-values like what was presented in the sepsis 3 paper
    y = target == 1
    P = len(preds)

    print('{:5s}'.format(''),end='\t')

    for p in range(P):
        print('{:20s}'.format(preds_header[p]),end='\t')

    print('')

    for p in range(P):
        ppred = preds_header[p]
        print('{:5s}'.format(ppred),end='\t')
        for q in range(P):
            qpred = preds_header[q]
            if ppred not in preds:
                print('{:20s}'.format(''),end='\t') # skip this as we do not have the prediction
            elif p==q:
                auc, ci = ru.calc_auc(preds[ppred], y, with_ci=True, alpha=0.05)
                print('{:0.3f} [{:0.3f}, {:0.3f}]'.format(auc, ci[0], ci[1]), end='\t')
            elif qpred not in preds:
                print('{:20s}'.format(''),end='\t') # skip this as we do not have the prediction
            elif q>p:
                alpha, ci = cronbach_alpha_bootstrap(np.row_stack([preds[ppred],preds[qpred]]),B=2000)
                print('{:0.3f} [{:0.3f}, {:0.3f}]'.format(alpha, ci[0], ci[1]), end='\t')
            else:
                pval, ci = ru.test_auroc(preds[ppred], preds[qpred], y)
                if pval > 0.001:
                    print('{:0.3f}{:15s}'.format(pval, ''), end='\t')
                else:
                    print('< 0.001{:15s}'.format(''),end='\t')


        print('')

def print_auc_table_to_file(preds, target, preds_header=None, filename=None):
    # prints a table of AUROCs and p-values
    # also train the baseline model using df_mdl
    # preds is a dictionary of predictions
    if filename is None:
        filename = 'auc_table.csv'

    f = open(filename,'w')


    if preds_header is None:
        preds_header = preds.keys()

    P = len(preds_header)
    y = target == 1
    f.write('{}\t'.format(''))

    # print header line
    for p in range(P):
        f.write('{}\t'.format(preds_header[p]))
    f.write('\n')

    for p in range(P):
        f.write('{}\t'.format(preds_header[p]))
        pname = preds_header[p]
        for q in range(P):
            qname = preds_header[q]
            if pname not in preds:
                f.write('{}\t'.format('')) # skip this as we do not have the prediction
            elif p==q:
                auc, ci = ru.calc_auc(preds[pname], y, with_ci=True, alpha=0.05)
                f.write('{:0.3f} [{:0.3f}, {:0.3f}]\t'.format(auc, ci[0], ci[1]))
            elif q>p:
                #TODO: cronenback alpha
                f.write('{}\t'.format(''))

            else:
                if qname not in preds:
                    f.write('{}\t'.format('')) # skip this as we do not have the prediction
                else:
                    pval, ci = ru.test_auroc(preds[pname], preds[qname], y)
                    if pval > 0.001:
                        f.write('{:0.3f}{}\t'.format(pval, ''))
                    else:
                        f.write('< 0.001{}\t'.format(''))


        f.write('\n')

    f.close()


def binomial_proportion(N, p, x1, x2):
    p = float(p)
    q = p/(1-p)
    k = 0.0
    v = 1.0
    s = 0.0
    tot = 0.0

    while(k<=N):
            tot += v
            if(k >= x1 and k <= x2):
                    s += v
            if(tot > 10**30):
                    s = s/10**30
                    tot = tot/10**30
                    v = v/10**30
            k += 1
            v = v*q*(N+1-k)/k
    return s/tot

# confidence intervals
def binomial_proportion_ci(numerator, denominator, alpha = 0.05):
    '''
    Calculate the confidence interval for a proportion of binomial counts.
    Confidence intervals calculated are symmetric.

    Sourced from @Kurtis from
    http://stackoverflow.com/questions/13059011/is-there-any-python-function-library-for-calculate-binomial-confidence-intervals
    ... which was based upon: http://statpages.info/confint.html
    ... which was further based upon:
        CJ Clopper and ES Pearson, "The use of confidence or fiducial limits
        illustrated in the case of the binomial." Biometrika. 26:404-413, 1934.

        F Garwood, "Fiducial Limits for the Poisson Distribution" Biometrica.
        28:437-442, 1936.
    '''
    numerator = float(numerator)
    denominator = float(denominator)
    p = alpha/2

    ratio = numerator/denominator
    if numerator==0:
            interval_low = 0.0
    else:
            v = ratio/2
            vsL = 0
            vsH = ratio

            while((vsH-vsL) > 10**-5):
                    if(binomial_proportion(denominator, v, numerator, denominator) > p):
                            vsH = v
                            v = (vsL+v)/2
                    else:
                            vsL = v
                            v = (v+vsH)/2
            interval_low = v

    if numerator==denominator:
            interval_high = 1.0
    else:
            v = (1+ratio)/2
            vsL = ratio
            vsH = 1
            while((vsH-vsL) > 10**-5):
                    if(binomial_proportion(denominator, v, 0, numerator) < p):
                            vsH = v
                            v = (vsL+v)/2
                    else:
                            vsL = v
                            v = (v+vsH)/2
            interval_high = v
    return (interval_low, interval_high)

def get_physiologic_data(con):
    query = 'SET search_path to ' + schema_name + ';' + \
    """
    with bg as
    (
    select
        icustay_id
        , min(PH) as ArterialPH_Min
        , max(PH) as ArterialPH_Max
        , min(PCO2) as PaCO2_Min
        , max(PCO2) as PaCO2_Max
        , min(PaO2FiO2) as PaO2FiO2_Min
        , min(AaDO2) as AaDO2_Min
    from bloodgasfirstdayarterial
    where SPECIMEN_PRED = 'ART'
    group by icustay_id
    )
    , vent as
    (
    select
        ie.icustay_id
        , max(case when vd.icustay_id is not null then 1 else 0 end)
            as MechVent
    from icustays ie
    left join ventdurations vd
        on ie.icustay_id = vd.icustay_id
        and vd.starttime <= ie.intime + interval '1' day
    group by ie.icustay_id
    )
    , vaso as
    (
    select
        ie.icustay_id
        , max(case when vd.icustay_id is not null then 1 else 0 end)
            as Vasopressor
    from icustays ie
    left join vasopressordurations vd
        on ie.icustay_id = vd.icustay_id
        and vd.starttime <= ie.intime + interval '1' day
    group by ie.icustay_id

    )
    select
        ie.icustay_id
        , vit.HeartRate_Min
        , vit.HeartRate_Max
        , vit.SysBP_Min
        , vit.SysBP_Max
        , vit.DiasBP_Min
        , vit.DiasBP_Max
        , vit.MeanBP_Min
        , vit.MeanBP_Max
        , vit.RespRate_Min
        , vit.RespRate_Max
        , vit.TempC_Min
        , vit.TempC_Max
        , vit.SpO2_Min
        , vit.SpO2_Max


        -- coalesce lab/vital sign glucose
        , case
            when vit.Glucose_min < lab.Glucose_Min
                then vit.Glucose_Min
            when lab.Glucose_Min < vit.Glucose_Min
                then lab.Glucose_Min
            else coalesce(vit.Glucose_Min, lab.Glucose_Min)
        end as Glucose_Min

        , case
            when vit.Glucose_Max > 2000 and lab.Glucose_Max > 2000
                then null
            when vit.Glucose_Max > 2000
                then lab.Glucose_Max
            when lab.Glucose_Max > 2000
                then vit.Glucose_Max
            when vit.Glucose_Max > lab.Glucose_Max
                then vit.Glucose_Max
            when lab.Glucose_Max > vit.Glucose_Max
                then lab.Glucose_Max
            else null
        end as Glucose_Max

        , gcs.MinGCS as GCS_Min

        -- height in centimetres
        , case
            when ht.Height > 100
             and ht.Height < 250
                 then ht.Height
            else null
        end as Height

        -- weight in kgs
        , case
            when wt.Weight > 30
             and wt.Weight < 300
                 then wt.Weight
            else null
        end as Height


        , lab.ANIONGAP_min
        , lab.ANIONGAP_max
        , lab.ALBUMIN_min
        , lab.ALBUMIN_max
        , lab.BANDS_min
        , lab.BANDS_max
        , lab.BICARBONATE_min
        , lab.BICARBONATE_max
        , lab.BILIRUBIN_min
        , lab.BILIRUBIN_max
        , lab.CREATININE_min
        , lab.CREATININE_max
        , lab.CHLORIDE_min
        , lab.CHLORIDE_max

        , lab.HEMATOCRIT_min
        , lab.HEMATOCRIT_max
        , lab.HEMOGLOBIN_min
        , lab.HEMOGLOBIN_max
        , lab.LACTATE_min
        , lab.LACTATE_max
        , lab.PLATELET_min
        , lab.PLATELET_max
        , lab.POTASSIUM_min
        , lab.POTASSIUM_max
        , lab.INR_min
        , lab.INR_max

        --, lab.PTT_min
        --, lab.PTT_max
        --, lab.PT_min
        --, lab.PT_max

        , lab.SODIUM_min
        , lab.SODIUM_max
        , lab.BUN_min
        , lab.BUN_max
        , lab.WBC_min
        , lab.WBC_max

        , rrt.RRT

        , case
            when uo.UrineOutput > 20000
                then null
            else uo.UrineOutput
        end as UrineOutput

        , vent.MechVent
        , vaso.Vasopressor

        , bg.AADO2_min
        , case
            when bg.PaO2FiO2_min > 1000
                then null
            else bg.PaO2FiO2_min
        end as PaO2FiO2_min
        , bg.ArterialPH_min
        , bg.ArterialPH_max
        , bg.PaCO2_min
        , bg.PaCO2_max

    from icustays ie
    left join vitalsfirstday vit
        on ie.icustay_id = vit.icustay_id
    left join gcsfirstday gcs
        on ie.icustay_id = gcs.icustay_id
    left join heightfirstday ht
        on ie.icustay_id = ht.icustay_id
    left join weightfirstday wt
        on ie.icustay_id = wt.icustay_id
    left join labsfirstday lab
        on ie.icustay_id = lab.icustay_id
    left join rrtfirstday rrt
        on ie.icustay_id = rrt.icustay_id
    left join uofirstday uo
        on ie.icustay_id = uo.icustay_id
    left join vent
        on ie.icustay_id = vent.icustay_id
    left join vaso
        on ie.icustay_id = vaso.icustay_id
    left join bg
        on ie.icustay_id = bg.icustay_id
    """

    dd = pd.read_sql_query(query,con)
    return dd
