"""Module for inferring the statistics of pandas objects."""

import warnings
from typing import Any, Dict, Union

import pandas as pd

from .checks import Check
from .dtypes import PandasDtype


NUMERIC_DTYPES = frozenset([
    PandasDtype.Float,
    PandasDtype.Float16,
    PandasDtype.Float32,
    PandasDtype.Float64,
    PandasDtype.Int,
    PandasDtype.Int8,
    PandasDtype.Int16,
    PandasDtype.Int32,
    PandasDtype.Int64,
    PandasDtype.UInt8,
    PandasDtype.UInt16,
    PandasDtype.UInt32,
    PandasDtype.UInt64,
    PandasDtype.DateTime,
])

# pylint: disable=unnecessary-lambda
STATISTICS_TO_CHECKS = {
    "min": lambda x: Check.greater_than_or_equal_to(x),
    "max": lambda x: Check.less_than_or_equal_to(x),
    "levels": lambda x: Check.isin(x),
}

CHECKS_TO_STATISTICS = {
    Check.greater_than_or_equal_to.__name__: (
        "min", lambda x: x.statistics["min_value"]),
    Check.less_than_or_equal_to.__name__: (
        "max", lambda x: x.statistics["max_value"]),
    Check.isin.__name__: (
        "levels", lambda x: x.statistics["allowed_values"]),
}


def infer_dataframe_statistics(df: pd.DataFrame) -> Dict[str, Any]:
    """Infer column and index statistics from a pandas DataFrame."""
    nullable_columns = df.isna().any()
    inferred_column_dtypes = {
        col: _get_array_type(df[col]) for col in df
    }
    column_statistics = {
        col: {
            "pandas_dtype": dtype,
            "nullable": nullable_columns[col],
            "checks": _get_array_check_statistics(df[col], dtype),
        }
        for col, dtype in inferred_column_dtypes.items()
    }
    return {
        "columns": column_statistics if column_statistics else None,
        "index": infer_index_statistics(df.index),
    }


def infer_series_statistics(series: pd.Series) -> Dict[str, Any]:
    """Infer column and index statistics from a pandas Series."""
    dtype = _get_array_type(series)
    return {
        "pandas_dtype": dtype,
        "nullable": series.isna().any(),
        "checks": _get_array_check_statistics(series, dtype),
        "name": series.name,
    }


def infer_index_statistics(index: Union[pd.Index, pd.MultiIndex]):
    """Infer index statistics given a pandas Index object."""

    def _index_stats(index_level):
        dtype = _get_array_type(index_level)
        return {
            "pandas_dtype": dtype,
            "nullable": index_level.isna().any(),
            "checks": _get_array_check_statistics(index_level, dtype),
            "name": index_level.name,
        }

    if isinstance(index, pd.MultiIndex):
        index_statistics = [
            _index_stats(index.get_level_values(i))
            for i in range(index.nlevels)
        ]
    elif isinstance(index, pd.Index):
        index_statistics = [_index_stats(index)]
    else:
        warnings.warn(
            "index type %s not recognized, skipping index inference" %
            type(index),
            UserWarning
        )
        index_statistics = []
    return index_statistics if index_statistics else None


def parse_check_statistics(check_stats: Union[Dict[str, Any], None]):
    """Convert check statistics to a list of Check objects."""
    if check_stats is None:
        return None
    checks = []
    for stat, create_check_fn in STATISTICS_TO_CHECKS.items():
        if stat not in check_stats:
            continue
        checks.append(create_check_fn(check_stats[stat]))
    return checks if checks else None


def get_dataframe_schema_statistics(dataframe_schema):
    """Get statistical properties from dataframe schema."""
    # pylint: disable=protected-access
    statistics = {
        "columns": {
            col_name: {
                "pandas_dtype": column._pandas_dtype,
                "nullable": column.nullable,
                "checks": parse_checks(column.checks),
            }
            for col_name, column in dataframe_schema.columns.items()
        },
        "index": (
            None if dataframe_schema.index is None else
            get_index_schema_statistics(dataframe_schema.index)
        ),
    }
    return statistics


def _get_series_base_schema_statistics(series_schema_base):
    # pylint: disable=protected-access
    return {
        "pandas_dtype": series_schema_base._pandas_dtype,
        "nullable": series_schema_base.nullable,
        "checks": parse_checks(series_schema_base.checks),
        "name": series_schema_base.name,
    }


def get_index_schema_statistics(index_schema_component):
    """Get statistical properties of index schema component."""
    try:
        # get index components from MultiIndex
        index_components = index_schema_component.indexes
    except AttributeError:
        index_components = [index_schema_component]
    return [
        _get_series_base_schema_statistics(index_component)
        for index_component in index_components
    ]


def get_series_schema_statistics(series_schema):
    """Get statistical properties from series schema."""
    return _get_series_base_schema_statistics(series_schema)


def parse_checks(checks) -> Union[Dict[str, Any], None]:
    """Convert Check object to check statistics."""
    check_statistics = {}
    _check_memo = {}
    for check in checks:
        stat_name, get_stat_fn = CHECKS_TO_STATISTICS.get(
            check.name, (None, None))
        if stat_name is not None and get_stat_fn is not None:
            check_statistics[stat_name] = get_stat_fn(check)
            _check_memo[stat_name] = check

    # raise ValueError on incompatible checks
    if "min" in check_statistics and "max" in check_statistics:
        min_value = check_statistics.get("min", float("-inf"))
        max_value = check_statistics.get("max", float("inf"))
        if min_value > max_value:
            raise ValueError(
                "checks %s and %s are incompatible, reason: "
                "min value %s > max value %s" % (
                    _check_memo["min"], _check_memo["max"],
                    min_value, max_value
                ))
    return check_statistics if check_statistics else None


def _get_array_type(x):
    # get most granular type possible
    dtype = PandasDtype.from_str_alias(str(x.dtype))
    # for object arrays, try to infer dtype
    if dtype is PandasDtype.Object:
        dtype = PandasDtype.from_pandas_api_type(
            pd.api.types.infer_dtype(x, skipna=True)
        )
    return dtype


def _get_array_check_statistics(
        x, dtype: PandasDtype) -> Union[Dict[str, Any], None]:
    """Get check statistics from an array-like object."""
    if dtype in NUMERIC_DTYPES or dtype is PandasDtype.DateTime:
        check_stats = {
            "min": x.min(),
            "max": x.max(),
        }
    elif dtype is PandasDtype.Category:
        try:
            categories = x.cat.categories
        except AttributeError:
            categories = x.categories
        check_stats = {
            "levels": categories.tolist(),
        }
    else:
        check_stats = {}
    return check_stats if check_stats else None