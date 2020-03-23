import json
import pravega.grpc_gateway as pravega
import time
import logging


def ignore_non_events(read_events):
    for read_event in read_events:
        if len(read_event.event) > 0:
            yield read_event

class StreamBase():
    def __init__(self, pravega_client, scope, stream, create=False):
        self.pravega_client = pravega_client
        self.scope = scope
        self.stream = stream
        if create:
            self.create_stream()

    def create_stream(self, min_num_segments=1):
        return self.pravega_client.CreateStream(pravega.pb.CreateStreamRequest(
            scope=self.scope,
            stream=self.stream,
            scaling_policy=pravega.pb.ScalingPolicy(min_num_segments=min_num_segments),
        ))

    def get_stream_info(self):
        return self.pravega_client.GetStreamInfo(pravega.pb.GetStreamInfoRequest(
            scope=self.scope,
            stream=self.stream,
        ))

    def truncate_stream(self):
        self.pravega_client.TruncateStream(pravega.pb.TruncateStreamRequest(
            scope=self.scope,
            stream=self.stream,
            stream_cut=self.get_stream_info().tail_stream_cut,
        ))


class UnindexedStream(StreamBase):
    def __init__(self, pravega_client, scope, stream):
        super(UnindexedStream, self).__init__(pravega_client, scope, stream)

    def read_events(self, from_stream_cut=None, to_stream_cut=None, stop_at_tail=False):
        if stop_at_tail:
            to_stream_cut = self.get_stream_info().tail_stream_cut
        read_events_request = pravega.pb.ReadEventsRequest(
            scope=self.scope,
            stream=self.stream,
            from_stream_cut=from_stream_cut,
            to_stream_cut=to_stream_cut,
        )
        return ignore_non_events(self.pravega_client.ReadEvents(read_events_request))


class UnindexedStream(StreamBase):
    def __init__(self, pravega_client, scope, stream):
        super(UnindexedStream, self).__init__(pravega_client, scope, stream)

    def read_events(self, from_stream_cut=None, to_stream_cut=None, stop_at_tail=False):
        if stop_at_tail:
            to_stream_cut = self.get_stream_info().tail_stream_cut
        read_events_request = pravega.pb.ReadEventsRequest(
            scope=self.scope,
            stream=self.stream,
            from_stream_cut=from_stream_cut,
            to_stream_cut=to_stream_cut,
        )
        return ignore_non_events(self.pravega_client.ReadEvents(read_events_request))

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
