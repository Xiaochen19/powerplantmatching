# -*- coding: utf-8 -*-
## Copyright 2015-2016 Fabian Hofmann (FIAS), Jonas Hoersch (FIAS)

## This program is free software; you can redistribute it and/or
## modify it under the terms of the GNU General Public License as
## published by the Free Software Foundation; either version 3 of the
## License, or (at your option) any later version.

## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.

## You should have received a copy of the GNU General Public License
## along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
Functions to modify and adjust power plant datasets
"""

from __future__ import absolute_import, print_function
import pandas as pd
import numpy as np
from .utils import (read_csv_if_string, lookup)
from .data import ENTSOE_stats
from .config import fueltype_to_life
from .cleaning import clean_single
# Logging: General Settings
import logging
logger = logging.getLogger(__name__)


def extend_by_non_matched(df, extend_by, label, fueltypes=None,
                          clean_added_data=True, use_saved_aggregation=False):
    """
    Returns the matched dataframe with additional entries of non-matched powerplants
    of a reliable source.

    Parameters
    ----------
    df : Pandas.DataFrame
        Already matched dataset which should be extended
    extend_by : pd.DataFrame
        Database which is partially included in the matched dataset, but
        which should be included totally
    label : str
        Column name of the additional database within the matched dataset, this
        string is used if the columns of the additional database do not correspond
        to the ones of the dataset
    """
    extend_by = read_csv_if_string(extend_by).set_index('projectID', drop=False)

    included_ids = df.projectID.map(lambda d: d.get(label)).dropna().sum()
    remaining_ids = extend_by.index.difference(included_ids)

    extend_by = extend_by.loc[remaining_ids]

    if fueltypes is not None:
        extend_by = extend_by[extend_by.Fueltype.isin(fueltypes)]
    if clean_added_data:
        extend_by = clean_single(extend_by, use_saved_aggregation=use_saved_aggregation, 
                                 dataset_name=label)
    extend_by = extend_by.rename(columns={'Name':label})
    extend_by['projectID'] = extend_by.projectID.map(lambda x: {label : x})
    return df.append(extend_by.loc[:, df.columns], ignore_index=True)


def rescale_capacities_to_country_totals(df, fueltypes):
    """
    Returns a extra column 'Scaled Capacity' with an up or down scaled capacity in
    order to match the statistics of the ENTSOe country totals. For every
    country the information about the total capacity of each fueltype is given.
    The scaling factor is determined by the ratio of the aggregated capacity of the
    fueltype within each coutry and the ENTSOe statistics about the fueltype capacity
    total within each country.

    Parameters
    ----------
    df : Pandas.DataFrame
        Data set that should be modified
    fueltype : str or list of strings
        fueltype that should be scaled
    """
    df = df.copy()
    if isinstance(fueltypes, str):
        fueltypes = [fueltypes]
    stats_df = lookup(df).loc[fueltypes]
    stats_entsoe = lookup(ENTSOE_stats()).loc[fueltypes]
    if ((stats_df==0)&(stats_entsoe!=0)).any().any():
        print('Could not scale powerplants in the countries %s because of no occurring \
              power plants in these countries'%\
              stats_df.loc[:, ((stats_df==0)&\
                            (stats_entsoe!=0)).any()].columns.tolist())
    ratio = (stats_entsoe/stats_df).fillna(1)
    df.loc[:, 'Scaled Capacity'] = df.loc[:, 'Capacity']
    for country in ratio:
        for fueltype in fueltypes:
            df.loc[(df.Country==country)&(df.Fueltype==fueltype), 'Scaled Capacity'] *= \
                   ratio.loc[fueltype,country]
    return df


def add_missing_capacities(df, fueltypes):
    """
    Primarily written to add artificial missing wind- and solar capacities to
    match these to the statistics.

    Parameters
    ----------
    df : Pandas.DataFrame
        Dataframe that should be modified
    fueltype : str or list of strings
        fueltype that should be scaled
    """
    df = df.copy()
    if isinstance(fueltypes, str):
        fueltypes = [fueltypes]
    stats_df = lookup(df).loc[fueltypes]
    stats_entsoe = lookup(ENTSOE_stats()).loc[fueltypes]
    missing = (stats_entsoe - stats_df).fillna(0.)
    missing[missing<0] = 0
    for country in missing:
        for fueltype in fueltypes:
            if missing.loc[fueltype, country] > 0:
                row = df.index.size
                df.loc[row, ['CARMA','ENTSOE','ESE','GEO','OPSD','WEPP','WRI']] = \
                             'Artificial_' + fueltype + '_' + country
                df.loc[row, 'Fueltype'] = fueltype
                df.loc[row, 'Country'] = country
                df.loc[row, 'Set'] = 'PP'
                df.loc[row, 'Capacity'] = missing.loc[fueltype,country]
                #TODO: This section needs to be enhanced, current values based on average construction year.
                if fueltype == 'Solar':
                    df.loc[row, 'YearCommissioned'] = 2011
                if fueltype == 'Wind':
                    df.loc[row, 'YearCommissioned'] = 2008
    return df


def average_empty_commyears(df):
    """
    Fills the empty commissioning years with averages.
    """
    df = df.copy()
    #mean_yrs = df.groupby(['Country', 'Fueltype']).YearCommissioned.mean().unstack(0)
    # 1st try: Fill with both country- and fueltypespecific averages
    df.YearCommissioned.fillna(df.groupby(['Country', 'Fueltype']).YearCommissioned
                               .transform("mean"), inplace=True)
    # 2nd try: Fill remaining with only fueltype-specific average
    df.YearCommissioned.fillna(df.groupby(['Fueltype']).YearCommissioned
                               .transform('mean'), inplace=True)
    # 3rd try: Fill remaining with only country-specific average
    df.YearCommissioned.fillna(df.groupby(['Country']).YearCommissioned
                               .transform('mean'), inplace=True)
    if df.YearCommissioned.isnull().any():
        count = len(df[df.YearCommissioned.isnull()])
        raise(ValueError('''There are still *{0}* empty values for 'YearCommissioned'
                            in the DataFrame. These should be either be filled
                            manually or dropped to continue.'''.format(count)))
    df.loc[:, 'YearCommissioned'] = df.YearCommissioned.astype(int)
    return df


def aggregate_RES_by_commyear(df, target_fueltypes=None):
    """
    Aggregates the vast number of RES (e.g. vom data.OPSD-RES()) units to one
    specific (Fueltype + Technology) cohorte per commissioning year.
    """
    df = df.copy()

    if target_fueltypes is None:
        target_fueltypes = ['Wind', 'Solar', 'Bioenergy']
    df = df[df.Fueltype.isin(target_fueltypes)]
    df = average_empty_commyears(df)
    df.Technology.fillna('-', inplace=True)
    df = df.groupby(['Country','YearCommissioned','Fueltype','Technology'])\
           .sum().reset_index().replace({'-': np.NaN})
    df.loc[:, 'Set'] = 'PP'
    return df


def aggregate_RES_by_yeardiff(df, target_fueltypes=None):
    df = df.copy()

    if target_fueltypes is None:
        target_fueltypes = ['Wind', 'Solar', 'Bioenergy']
    df = df[df.Fueltype.isin(target_fueltypes)]
    df.loc[:, 'Addition'] = np.NaN

    for c, df_country in df.groupby(['Country']):
        for tech, stats in df_country.groupby(['Technology']):
            if (stats.Year.diff().fillna(1) != 1).any():
                logger.warn('The year column for country "{0}" and '
                                 'technology "{1}" is not continuous.'.format(c, tech))
            stats.loc[:, 'Addition'] = stats.Capacity.diff().fillna(stats.Capacity.iloc[0])
            df.update(stats)

    df.drop(['Capacity'], axis=1, inplace=True)
    df = df.rename(columns={'Year': 'YearCommissioned',
                            'Addition': 'Capacity'})
    return df.reset_index(drop=True)


def derive_vintage_cohorts_from_statistics(df, base_year=2015):
    """
    This function assumes an age-distribution for given capacity statistics
    and returns a df, containing how much of capacity has been built for every
    year.
    """
    def setInitial_Flat(mat, df, life):
        y_start = df.index[0]
        height_flat = float(df.loc[y_start].Capacity) / life
        for y in range(int(mat.index[0]), y_start+1):
            y_end = min(y+life-1, mat.columns[-1])
            mat.loc[y, y:y_end] = height_flat
        return mat

    def setInitial_Triangle(mat, df, life):
        y_start = df.index[0]
        years = range(y_start-life+1, y_start+1)
        height_flat = float(df.loc[y_start].Capacity) / life
        decr = 2.0*height_flat/life              # decrement per period, 'slope' of the triangle
        height_tri = 2.0*height_flat - decr/2.0  # height of triangle at right side
        series = [(height_tri - i*decr) for i in range(0, life)][::-1]
        dic = dict(zip(years, series))           # create dictionary
        for y in range(int(mat.index[0]), y_start+1):
            y_end = min(y+life-1, mat.columns[-1])
            mat.loc[y, y:y_end] = dic[y]
        return mat

    def setHistorical(mat, df, life):
        year = df.index[1]  # Base year was already handled in setInitial()->Start one year later.
        while year <= df.index.max():
            if year in df.index:
                addition = df.loc[year].Capacity - mat.loc[:, year].sum()
                if addition >= 0:
                    mat.loc[year, year:year+life-1] = addition
                else:
                    mat.loc[year, year:year+life-1] = 0
                    mat = reduceVintages(addition, mat, life, year)
            else:
                mat.loc[year, year:year+life-1] = 0
            year += 1
        return mat

    def reduceVintages(addition, mat, life, y_pres):
        for year in mat.index:
            val_rem = float(mat.loc[year, y_pres])
            # print ('In year %i are %.2f units left from year %i, while addition '\
            #        'delta is %.2f'%(y_pres, val_rem, year, addition))
            if val_rem > 0:
                if abs(addition) > val_rem:
                    mat.loc[year, y_pres:year+life-1] = 0
                    addition += val_rem
                else:
                    mat.loc[year, y_pres:year+life-1] = val_rem + addition
                    break
        return mat

    dfe = pd.DataFrame(columns=df.columns)
    for c, df_country in df.groupby(['Country']):
        for tech, dfs in df_country.groupby(['Technology']):
            dfs.set_index('Year', drop=False, inplace=True)
            y_start = dfs.index[0]
            y_end = dfs.index[-1]
            life = fueltype_to_life()[dfs.Fueltype.iloc[0]]
            mat = pd.DataFrame(columns=range(y_start-life+1, y_end+life),
                               index=range(y_start-life+1, y_end)).astype(np.float)
            if dfs.Fueltype.iloc[0] in ['Solar', 'Wind', 'Bioenergy', 'Geothermal']:
                mat = setInitial_Triangle(mat, dfs, life)
            else:
                mat = setInitial_Flat(mat, dfs, life)
            if y_end > y_start:
                mat = setHistorical(mat, dfs, life)
            add = pd.DataFrame(columns=dfs.columns)
            add.Capacity = list(mat.loc[:, base_year])
            add.Year = mat.index.tolist()
            add.Technology = tech
            add.Country = c
            add.Fueltype = dfs.Fueltype.iloc[0]
            add.Set = dfs.Set.iloc[0]
            dfe = pd.concat([dfe, add[add.Capacity>0.0]], ignore_index=True)
    dfe.Year = dfe.Year.apply(pd.to_numeric)
    dfe.rename(columns={'Year':'YearCommissioned'}, inplace=True)
    return dfe[~np.isclose(dfe.Capacity, 0)]









#add artificial powerplants
#entsoe = pc.ENTSOE_data()
#lookup = pc.lookup([entsoe.loc[entsoe.Fueltype=='Hydro'], hydro], keys= ['ENTSOE', 'matched'], by='Country')
#lookup.loc[:,'Difference'] = lookup.ENTSOE - lookup.matched
#missingpowerplants = (lookup.Difference/120).round().astype(int)
#
#hydroexp = hydro
#
#for i in missingpowerplants[:-1].loc[missingpowerplants[:-1] > 0].index:
#    print i
#    try:
#        howmany = missingpowerplants.loc[i]
#        hydroexp = hydroexp.append(hydro.loc[(hydro.Country == i)& (hydro.lat.notnull()),['lat', 'lon']].sample(howmany) + np.random.uniform(-.4,.4,(howmany,2)), ignore_index=True)
#        hydroexp.loc[hydroexp.shape[0]-howmany:,'Country'] = i
#        hydroexp.loc[hydroexp.shape[0]-howmany:,'Capacity'] = 120.
#        hydroexp.loc[hydroexp.shape[0]-howmany:,'FIAS'] = 'Artificial Powerplant'
#
#
#    except:
#        for j in range(missingpowerplants.loc[i]):
#            hydroexp = hydroexp.append(hydro.loc[(hydro.Country == i)& (hydro.lat.notnull()),['lat', 'lon']].sample(1) + np.random.uniform(-1,1,(1,2)), ignore_index=True)
#            hydroexp.loc[hydroexp.shape[0]-1:,'Country'] = i
#            hydroexp.loc[hydroexp.shape[0]-1:,'Capacity'] = 120.
#            hydroexp.loc[hydroexp.shape[0]-howmany:,'FIAS'] = 'Artificial Powerplant'
#
#for i in missingpowerplants[:-1].loc[missingpowerplants[:-1] < -1].index:
#    while hydroexp.loc[hydroexp.Country == i, 'Capacity'].sum() > lookup.loc[i, 'ENTSOE'] + 300:
#        try:
#            hydroexp = hydroexp.drop(hydroexp.loc[(hydroexp.Country == i)& (hydroexp.GEO.isnull())].sample(1).index)
#        except:
#            hydroexp = hydroexp.drop(hydroexp.loc[(hydroexp.Country == i)].sample(1).index)
#
#hydroexp.Fueltype = 'Hydro'
#pc.lookup([entsoe.loc[entsoe.Fueltype=='Hydro'], hydroexp], keys= ['ENTSOE', 'matched'], by='Country')
#
#del hydro
#hydro = hydroexp
#
#print hydro.groupby(['Country', 'Technology']).Capacity.sum().unstack()
