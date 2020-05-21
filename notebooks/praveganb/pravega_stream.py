import IPython
import base64
import io
import json
import numpy as np
import pandas as pd
import pravega.grpc_gateway as pravega
from matplotlib import pyplot as plt
import time
import logging
from itertools import islice, cycle
import threading
import traceback
import random
import glob

def ignore_non_events(read_events):
    for read_event in read_events:
        if len(read_event.event) > 0:
            yield read_event

class StreamBase():
    def __init__(self, pravega_client, scope, stream):
        self.pravega_client = pravega_client
        self.scope = scope
        self.stream = stream

    def create_stream(self, min_num_segments=1):
        return self.pravega_client.CreateStream(pravega.pb.CreateStreamRequest(
            scope=self.scope,
            stream=self.stream,
            scaling_policy=pravega.pb.ScalingPolicy(min_num_segments=min_num_segments),
        ))

    def delete_stream(self):
        return self.pravega_client.DeleteStream(pravega.pb.DeleteStreamRequest(
            scope=self.scope,
            stream=self.stream,
        ))

    def get_stream_info(self):
        return self.pravega_client.GetStreamInfo(pravega.pb.GetStreamInfoRequest(
            scope=self.scope,
            stream=self.stream,
        ))

    def truncate_stream(self):
        return self.pravega_client.TruncateStream(pravega.pb.TruncateStreamRequest(
            scope=self.scope,
            stream=self.stream,
            stream_cut=self.get_stream_info().tail_stream_cut,
        ))

    def write_events(self, events_to_write):
        return self.pravega_client.WriteEvents(events_to_write)


class IndexStream(StreamBase):
    """Represents a Pravega Stream that stores a Stream Cut index for another Pravega Stream."""
    def index_record_write_generator(self, index_records):
        for index_record in index_records:
            rec = index_record.copy()
            rec['event_pointer'] = base64.b64encode(rec['event_pointer']).decode(encoding='UTF-8')
            rec_bytes = json.dumps(rec).encode(encoding='UTF-8')
            event_to_write = pravega.pb.WriteEventsRequest(
                scope=self.scope,
                stream=self.stream,
                event=rec_bytes,
                routing_key='',
            )
            yield event_to_write

    def append_index_records(self, index_records, truncate=False):
        if truncate:
            self.truncate_stream()
        events_to_write = self.index_record_write_generator(index_records)
        self.pravega_client.WriteEvents(events_to_write)

    def read_index_records(self):
        stream_info = self.get_stream_info()
        read_events_request = pravega.pb.ReadEventsRequest(
            scope=self.scope,
            stream=self.stream,
            from_stream_cut=stream_info.head_stream_cut,
            to_stream_cut=stream_info.tail_stream_cut,
        )
        return (json.loads(read_event.event) for read_event in self.pravega_client.ReadEvents(read_events_request))


class UnindexedStream(StreamBase):
    def __init__(self, pravega_client, scope, stream):
        super(UnindexedStream, self).__init__(pravega_client, scope, stream)

        
    def read_events(self, from_stream_cut=None, to_stream_cut=None, stop_at_tail=False):
        """Read events from a Pravega stream. Returned events will be byte arrays."""
        if stop_at_tail:
            to_stream_cut = self.get_stream_info().tail_stream_cut
        read_events_request = pravega.pb.ReadEventsRequest(
            scope=self.scope,
            stream=self.stream,
            from_stream_cut=from_stream_cut,
            to_stream_cut=to_stream_cut,
        )
        return self.pravega_client.ReadEvents(read_events_request)
    

    def read_event_tojson(self, read_event):
        event_json = read_event.event
        return (json.loads(event_json))

    def read_events_from_stream(self, from_stream_cut=None, to_stream_cut=None, stop_at_tail=False):
        read_events = self.read_events(from_stream_cut, to_stream_cut, stop_at_tail=stop_at_tail)
        return (self.read_event_tojson(read_event) for read_event in read_events)

    def play_stream(self, from_stream_cut=None, to_stream_cut=None):
        read_events = self.read_events_from_stream(from_stream_cut, to_stream_cut)
        for i, event in enumerate(read_events):
            print(event)

class IndexedStream(UnindexedStream):
    def __init__(self, pravega_client, scope, stream, timestamp_col='Timestamp', exclude_cols=('MetricValues', 'ReportSequence', 'Name', '@odata.type', '@odata.id', '@odata.context',)):
        super(IndexedStream, self).__init__(pravega_client, scope, stream)
        self.pravega_client = pravega_client
        self.scope = scope
        self.stream = stream
        self.timestamp_col = timestamp_col
        self.exclude_cols = exclude_cols
        self.index_df = None
        self.index_stream = IndexStream(pravega_client, scope, '%s-index' % stream)
        self.index_stream.create_stream()

    def index_builder(self, force_full=False, stop_at_tail=True):
        if self.index_df is None and not force_full:
            self.load_index()
        stream_info = self.get_stream_info()
        if self.index_df is None or force_full:
            print('index_builder: Performing full index generation')
            from_stream_cut = stream_info.head_stream_cut
            truncate = True
        else:
            print('index_builder: Performing incremental index update')
            from_stream_cut = pravega.pb.StreamCut(text=self.index_df.iloc[-1].to_stream_cut)
            truncate = False
        if stop_at_tail:
            to_stream_cut = stream_info.tail_stream_cut
        else:
            to_stream_cut = None
        read_events_request = pravega.pb.ReadEventsRequest(
            scope=self.scope,
            stream=self.stream,
            from_stream_cut=from_stream_cut,
            to_stream_cut=to_stream_cut,
        )
        print('index_builder: Reading stream %s and writing index stream %s' % (self.stream, self.index_stream.stream))
        read_events = self.pravega_client.ReadEvents(read_events_request)
        index_records = self.index_record_generator(read_events, from_stream_cut.text)
        self.index_stream.append_index_records(index_records, truncate=truncate)

    def continuous_index_builder(self, force_full=False):
        self.index_builder(force_full=force_full, stop_at_tail=False)

    def update_index(self, force_full=False):
        self.index_builder(force_full=force_full, stop_at_tail=True)
        self.load_index()

    def index_record_generator(self, read_events, from_stream_cut):
        for read_event in read_events:
            rec = self.read_event_to_index(read_event)
            rec['from_stream_cut'] = from_stream_cut
            from_stream_cut = rec['to_stream_cut']
            yield rec

    def read_event_to_index(self, read_event):
        """Convert an event to a record that will be written to the index stream."""
        event_json = read_event.event
        event = json.loads(event_json)
        for col in self.exclude_cols:
            if col in event: del event[col]
        event['to_stream_cut'] = read_event.stream_cut.text
        event['event_pointer'] = read_event.event_pointer.bytes
        return event

    def load_index(self, limit=None):
        print('load_index: Reading index stream %s' % self.index_stream.stream)
        index_records = self.index_stream.read_index_records()
        if limit:
            index_records = islice(index_records, limit)
        index_records = list(index_records)
        if len(index_records) > 0:
            df = pd.DataFrame(index_records)
            df[self.timestamp_col] = pd.to_datetime(df[self.timestamp_col], format="%Y-%m-%dT%H:%M:%S.%fZ")
            df = df.set_index([self.timestamp_col])
            self.index_df = df
        else:
            self.index_df = None

    def get_stream_cuts_for_time_window(self, ts0, ts1=None, count=None):
        """Get a pair of from and to stream cuts that can be used to read events starting at timestamp ts0 (inclusive).
        If count is specified, this many events will be read.
        Otherwise, reading will continue until timestamp ts1 (inclusive)."""
        from_index = self.index_df.index.searchsorted(ts0, side='left')
        if count is None:
            if ts1 is None: ts1 = ts0
            to_index = self.index_df.index.searchsorted(ts1, side='right') - 1
            if to_index < 0: raise Exception('End timestamp %s not found' % str(ts1))
        else:
            to_index = from_index + count - 1
        from_index_record = self.index_df.iloc[from_index]
        to_index_record = self.index_df.iloc[to_index]
        from_stream_cut = pravega.pb.StreamCut(text=from_index_record.from_stream_cut)
        to_stream_cut = pravega.pb.StreamCut(text=to_index_record.to_stream_cut)
        return pd.Series(dict(
            from_index=from_index,
            to_index=to_index,
            from_timestamp=from_index_record.name,
            to_timestamp=to_index_record.name,
            from_stream_cut=from_stream_cut,
            to_stream_cut=to_stream_cut))

    def get_event_pointer_for_timestamp(self, timestamp):
        from_index = self.index_df.index.searchsorted(timestamp, side='left')
        from_index_record = self.index_df.iloc[from_index]
        return from_index_record

    def read_events_in_time_window(self, ts0, ts1):
        stream_cuts = self.get_stream_cuts_for_time_window(ts0, ts1)
        read_events_request = pravega.pb.ReadEventsRequest(
            scope=self.scope,
            stream=self.stream,
            from_stream_cut=stream_cuts.from_stream_cut,
            to_stream_cut=stream_cuts.to_stream_cut,
        )
        return self.pravega_client.ReadEvents(read_events_request)

