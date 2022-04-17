import datetime
import functools
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
import warnings

import pandas as pd

from great_expectations.core.batch import RuntimeBatchRequest
from great_expectations.marshmallow__shade.fields import Bool
from great_expectations.validator.validator import Validator
from great_expectations.core import ExpectationSuite
from great_expectations.datasource.new_datasource import Datasource

#!!! Factor this out to somewhere nicer
class GxExperimentalWarning(Warning):
    pass

def _add_gx_args(
    func:Callable=None,
    primary_arg_variable_name:str=None,
    default_use_primary_arg_as_id:bool=None,
    arguments_excluded_from_runtime_parameters:Dict[str, int]={},
):

    if func is None:
        return functools.partial(
            _add_gx_args,
            primary_arg_variable_name=primary_arg_variable_name,
            default_use_primary_arg_as_id=default_use_primary_arg_as_id,
            arguments_excluded_from_runtime_parameters=arguments_excluded_from_runtime_parameters,
        )

    def wrapped(
        self,
        primary_arg : Optional[Any] = None,
        *args,
        id_ : Optional[str] = None,
        use_primary_arg_as_id : Optional[Bool] = default_use_primary_arg_as_id,
        expectation_suite : Optional[ExpectationSuite] = None,
        timestamp = None,
        **kwargs,
    ):

        if id_ is not None and use_primary_arg_as_id == True:
            raise ValueError(
                "id_ cannot be specified when use_primary_arg_as_id is also True"
            )

        if id_ is None:
            if use_primary_arg_as_id == None:
                use_primary_arg_as_id = self._decide_whether_to_use_variable_as_identifier(primary_arg)

            if use_primary_arg_as_id:
                id_ = primary_arg
            else:
                id_ = None

        if timestamp == None:
            timestamp  = datetime.datetime.now()

        #!!! Check to ensure serializability of args and kwargs.
        # Non-serializable args and kwargs should be replaced by some token that indicates that they were present, but can't be saved.
        # https://stackoverflow.com/questions/51674222/how-to-make-json-dumps-in-python-ignore-a-non-serializable-field
        # Alternatively, maybe we just make BatchRequests serialization-safe, and worry about it on the output side.

        if primary_arg_variable_name in kwargs:
            if primary_arg != None:
                raise TypeError(f'{func.__name__}() got multiple values for argument {primary_arg_variable_name}')

            primary_arg = kwargs.pop(primary_arg_variable_name)

        df = func(
            self,
            primary_arg,
            *args,
            **kwargs
        )

        args, kwargs = self._remove_excluded_arguments(
            arguments_excluded_from_runtime_parameters,
            args,
            kwargs,
        )

        batch = self.get_single_batch_from_batch_request(
            batch_request=RuntimeBatchRequest(
                datasource_name=self.name,
                data_connector_name="runtime_data_connector",
                data_asset_name="default_data_asset",
                runtime_parameters={
                    "batch_data": df,
                    "args": list(args),
                    "kwargs": kwargs,
                },
                batch_identifiers={
                    "id_": id_,
                    "timestamp": timestamp,
                },
            )
        )

        #!!! Returning a Validator goes against the pattern we've used elsewhere for Datasources.
        #I'm increasingly convinced that this is the right move, rather than returning Batches, which are useless objects.
        validator = Validator(
            execution_engine = self.execution_engine,
            expectation_suite=expectation_suite,
            batches = [batch],
        )

        return validator

    return wrapped

class PandasReaderDatasource(Datasource):
    """
    This class provides thin wrapper methods for all of pandas' `read_*` methods.

    There's no other concept of DataConnector configuration.

    This class is intended to enable very simple syntaax for users who are just getting started with Great Expectations, and have not configured (or learned about) Datasources and DataConnectors.
        my_context.datasources.pandas_readers.read_csv

    The idea is to include this Datasource as a default datasource for new LiteDataContexts.

    Notes on pandas read_* methods:

    Almost all of pandas.read_* methods require a single positional argument. In general, they are one of the following:
        1. path-type objects (eg filepaths, URLs) that point to data that can be parsed into a DataFrame,
        2. the contents of a thing to be parsed into a DataFrame (e.g. a JSON string), or
        3. buffer/file-like objects that can be read and parsed into a DataFrame.

    Exceptions are listed here:

        pandas.read_pickle
        pandas.read_table
        pandas.read_csv
        pandas.read_fwf
        pandas.read_excel
        pandas.read_json : The first positional argument is optional. It's not clear from the docs how that works...
        pandas.read_html
        pandas.read_xml
        pandas.read_hdf
        pandas.read_sas
        pandas.read_gbq
        pandas.read_stata
        pandas.read_clipboard : Doesn't have a required positional argument
        pandas.read_feather : Always a path
        pandas.read_parquet : Always a path
        pandas.read_orc : Always a path
        pandas.read_spss : Always a path
        pandas.read_sql_table : Requires a table name (~"always a path") and a connection
        pandas.read_sql_query : Requires a query and a connection
        pandas.read_sql : Requires a query or table name, and a connection

    For case 1, we can use the path-type object itself as the identifier, plus a time stamp.
    For cases 2 and 3, we really have no information about the provenance of the object. Instead, we realy entirely on the time stamp to identify the batch.

    (How can we detect the difference between these cases?)

    In both cases, we'll want to provide the option of capturing any serializable kwargs as additional metadata about provenance.
    """

    def __init__(
        self,
        name,
    ):
        #!!! Trying this on for size
        # experimental-v0.15.1
        # warnings.warn(
        #     "\n================================================================================\n" \
        #     "PandasReaderDatasource is an experimental feature of Great Expectations\n" \
        #     "You should consider the API to be unstable.\n" \
        #     "If you have questions or feedback, please chime in at\n" \
        #     "https://github.com/great-expectations/great_expectations/discussions/DISCUSSION-ID-GOES-HERE\n" \
        #     "================================================================================\n",
        #     GxExperimentalWarning,
        # )

        super().__init__(
            name=name,
            execution_engine={
                "class_name": "PandasExecutionEngine",
                "module_name": "great_expectations.execution_engine",
            },
            data_connectors={
                "runtime_data_connector": {
                    "class_name": "RuntimeDataConnector",
                    "batch_identifiers": [
                        "id_",
                        "timestamp",
                    ],
                }
            },
        )

    def _decide_whether_to_use_variable_as_identifier(self, var):
        #!!! This is brittle. Almost certainly needs fleshing out.
        if not isinstance(var, str):
            return False
        
        # Does the string contain any whitespace?
        return re.search('\s', var) == None

    def _remove_excluded_arguments(
        self,
        arguments_excluded_from_runtime_parameters:Dict[str, int],
        args:List[Any],
        kwargs:Dict[str, Any],
    ) -> Tuple[ List[Any], Dict[str, Any] ]:
        remove_indices = []
        for arg_name, index in arguments_excluded_from_runtime_parameters.items():
            kwargs.pop(arg_name, None)
            remove_indices.append(index-1)

        args = [i for j, i in enumerate(args) if j not in remove_indices]

        return args, kwargs



    @_add_gx_args(primary_arg_variable_name="filepath_or_buffer")
    def read_csv(self, primary_arg, *args, **kwargs):
        return pd.read_csv(primary_arg, *args, **kwargs)
       
    @_add_gx_args(primary_arg_variable_name="path_or_buf", default_use_primary_arg_as_id=False)
    def read_json(self, primary_arg, *args, **kwargs):
        return pd.read_json(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="filepath_or_buffer")
    def read_table(self, filepath_or_buffer, *args, **kwargs):
        return pd.read_table(filepath_or_buffer, *args, **kwargs)

    @_add_gx_args(default_use_primary_arg_as_id=False)
    def read_clipboard(self, *args, **kwargs):
        return pd.read_clipboard(*args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="filepath_or_buffer")
    def from_dataframe(self, primary_arg, *args, **kwargs):
        def no_op(df):
            return df
        
        return no_op(primary_arg)

    ### These three methods take a connection as their second argument.

    @_add_gx_args(primary_arg_variable_name="table_name", default_use_primary_arg_as_id=True, arguments_excluded_from_runtime_parameters={"con":1})
    def read_sql_table(self, primary_arg, *args, **kwargs):
        return pd.read_sql_table(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="sql", default_use_primary_arg_as_id=False, arguments_excluded_from_runtime_parameters={"con":1})
    def read_sql_query(self, primary_arg, *args, **kwargs):
        return pd.read_sql_query(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="sql", arguments_excluded_from_runtime_parameters={"con":1})
    def read_sql(self, primary_arg, *args, **kwargs):
        return pd.read_sql(primary_arg, *args, **kwargs)


    #!!! Methods below this line aren't yet tested
 
    @_add_gx_args(primary_arg_variable_name="filepath_or_buffer")
    def read_pickle(self, primary_arg, *args, **kwargs):
        return pd.read_pickle(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="filepath_or_buffer")
    def read_fwf(self, primary_arg, *args, **kwargs):
        return pd.read_fwf(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="io")
    def read_excel(self, primary_arg, *args, **kwargs):
        return pd.read_excel(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="io")
    def read_html(self, primary_arg, *args, **kwargs):
        return pd.read_html(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="path_or_buffer")
    def read_xml(self, primary_arg, *args, **kwargs):
        return pd.read_xml(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="path_or_buf")
    def read_hdf(self, primary_arg, *args, **kwargs):
        return pd.read_hdf(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="filepath_or_buffer")
    def read_sas(self, primary_arg, *args, **kwargs):
        return pd.read_sas(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="query", default_use_primary_arg_as_id=False)
    def read_gbq(self, primary_arg, *args, **kwargs):
        return pd.read_gbq(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="filepath_or_buffer")
    def read_stata(self, primary_arg, *args, **kwargs):
        return pd.read_stata(primary_arg, *args, **kwargs)

    ### These next methods always take a path as their primary argument (buffers not allowed).

    @_add_gx_args(primary_arg_variable_name="path", default_use_primary_arg_as_id=True)
    def read_feather(self, primary_arg, *args, **kwargs):
        return pd.read_feather(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="path", default_use_primary_arg_as_id=True)
    def read_parquet(self, primary_arg, *args, **kwargs):
        return pd.read_parquet(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="path", default_use_primary_arg_as_id=True)
    def read_orc(self, primary_arg, *args, **kwargs):
        return pd.read_orc(primary_arg, *args, **kwargs)

    @_add_gx_args(primary_arg_variable_name="path", default_use_primary_arg_as_id=True)
    def read_spss(self, primary_arg, *args, **kwargs):
        return pd.read_spss(primary_arg, *args, **kwargs)


    #!!! Leaving this commented out, in case we have second thoughts about the decorator
    # def read_csv(
    #     self,
    #     primary_arg : Any,
    #     *args,
    #     id_ : Optional[str] = None,
    #     use_primary_arg_as_id : Optional[Bool] = None,
    #     expectation_suite : Optional[ExpectationSuite] = None,
    #     timestamp = None,
    #     **kwargs,
    # ) -> Validator:
    #     #!!! This whole top section could be put into a decorator
    #     if id_ is not None and use_primary_arg_as_id == True:
    #         raise ValueError(
    #             "id_ cannot be specified when use_primary_arg_as_id is also True"
    #         )

    #     if id_ is None:
    #         if use_primary_arg_as_id == None:
    #             use_primary_arg_as_id = self._decide_whether_to_use_variable_as_identifier(primary_arg)

    #         if use_primary_arg_as_id:
    #             id_ = primary_arg
    #         else:
    #             id_ = None

    #     if timestamp == None:
    #         timestamp  = datetime.datetime.now()

    #     #!!! Check to ensure serializability of args and kwargs.
    #     # Non-serializable args and kwargs should be replaced by some token that indicates that they were present, but can't be saved.
    #     # https://stackoverflow.com/questions/51674222/how-to-make-json-dumps-in-python-ignore-a-non-serializable-field

    #     df = pd.read_csv(
    #         primary_arg, 
    #         *args,
    #         **kwargs
    #     )
    
    #     #!!! This bottom section could be put into a decorator, too
    #     batch = self.get_single_batch_from_batch_request(
    #         batch_request=RuntimeBatchRequest(
    #             datasource_name=self.name,
    #             data_connector_name="runtime_data_connector",
    #             data_asset_name="default_data_asset",
    #             runtime_parameters={
    #                 "batch_data": df,
    #                 "args": list(args),
    #                 "kwargs": kwargs,
    #             },
    #             batch_identifiers={
    #                 "id_": id_,
    #                 "timestamp": timestamp,
    #             },
    #         )
    #     )

    #     #!!! Returning a Validator goes against the pattern we've used elsewhere for Datasources.
    #     #I'm increasingly convinced that this is the right move, rather than returning Batches, which are useless objects.
    #     validator = Validator(
    #         execution_engine = self.execution_engine,
    #         expectation_suite=expectation_suite,
    #         batches = [batch],
    #     )

    #     return validator
