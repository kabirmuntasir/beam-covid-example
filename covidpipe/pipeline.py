
from typing import Dict
from typing import Set

import json

import apache_beam as beam
from apache_beam.options import pipeline_options

import covidpipe
from covidpipe.transforms import FindStateSpikesFn
from covidpipe.options import CovidTrackingPipelineOptions


def run(options: pipeline_options.PipelineOptions):

  p =  beam.Pipeline(options=options)

  # Read in the CSV file
  input_data = read_data(
      p, options.view_as(CovidTrackingPipelineOptions).input_file)

  # Analyze the data: Find columns are present in every row, and columns that
  # aren't.
  column_information = beam.pvalue.AsSingleton(
      input_data
      | covidpipe.datasource.FindEmptyAndNonEmptyColumns()
  )

  # Get columns from our rows, and also ensure we get the main columns of
  # interest.
  full_data = select_wanted_columns(
      input_data, column_information, ['positive', 'negative'])

  # Filter out data points without 'positive' field. This is because they are
  # not valuable for our analysis.
  filtered_data = full_data | 'FilterMissingPositive' >> beam.Filter(
      lambda x: 'positive' in x)

  # For each state, let's get an iterable of
  per_state_iterables = (
      filtered_data
      | beam.WithKeys(lambda x: x['state'])
      | beam.GroupByKey()
      | beam.Values()
  )

  # Find 7-day spikes per state
  state_spikes = (
      per_state_iterables
      | beam.ParDo(FindStateSpikesFn()))

  # Write spikes to an output
  (state_spikes
   | beam.Map(json.dumps)
   | beam.io.WriteToText(
      options.view_as(CovidTrackingPipelineOptions).spikes_output_file))

  result = p.run()


#### After this point, there are transforms used in  the main pipeline
def read_data(pipeline, input_file):
  return pipeline | covidpipe.datasource.ReadFromCsv(input_file)


def select_wanted_columns(input_data, column_information, extra_columns):
  def select_wanted_columns(row: Dict[str, str],
      column_info: Dict[str, Set[str]]):
    empty_columns = set(column_info[
                          covidpipe.datasource.FindEmptyAndNonEmptyColumns.EMPTY])

    sanitized_row = {k: v for k, v in row.items()
                     if (k not in empty_columns or k in extra_columns) and v}

    # If the row does not contain any values, then we must discard it.
    if sanitized_row:
      yield sanitized_row

  return input_data | 'SelectColumns' >> beam.FlatMap(select_wanted_columns,
                                                      column_information)


#### After this point, the pipeline is set up to run
if __name__ == '__main__':
  import sys
  options = pipeline_options.PipelineOptions(sys.argv[1:])
  run(options)