from praveganb.pravega_stream import *
import grpc
import simplejson as json
import math
import time
import datetime
import tzlocal
import collections
import pandas
from scipy.stats import norm
from scipy import stats
import numpy as np



enki = [
    '10.243.19.25',
    '10.243.19.26',
    '10.243.19.27',
    '10.243.19.28',
    '10.243.19.29',
    '10.243.19.172',
    '10.243.19.173',
    '10.243.19.174',
    '10.243.19.175',
    '10.243.19.176',
    '10.243.19.177',
    '10.243.19.178',
    '10.243.19.179',
    '10.243.19.180',
]

tiltedbarn = [
    '10.243.61.14',
    '10.243.61.15',
    '10.243.61.16',
    '10.243.61.17'
]

nightshift = [
    '10.243.19.198',
    '10.243.19.199',
    '10.243.19.200',
    '10.243.19.201',
    '10.243.19.202',
    '10.243.19.203',
    '10.243.19.204',
    '10.243.19.205',
    '10.243.19.206',
    '10.243.19.207',
    '10.243.19.208',
    '10.243.19.209',
    '10.243.19.210',
    '10.243.19.211',
    '10.243.19.212'
]

anchorsteam = [
    '10.243.19.152',
    '10.243.19.153',
    '10.243.19.154',
    '10.243.19.155',
    '10.243.19.156'
    '10.243.19.157',
    '10.243.19.158'
              ]
eaglemonk = [ 
    '10.243.19.70',
    '10.243.19.72', 
    '10.243.19.74' , 
    '10.243.19.76', 
    '10.243.19.78', 
    '10.243.19.80', 
    '10.243.19.82',
    '10.243.19.84'
            ]

racks = dict()
racks['anchorsteam'] = anchorsteam
racks['eaglemonk'] = eaglemonk
racks['nightshift'] = nightshift
racks['tiltedbarn'] = tiltedbarn
racks['enki'] = enki

def calculate_stdv(reports):
    s1 = 0
    s2 = 0
    count = 0
    mean = 0
    for report in reports:
        s1 += report['avg']
        s2 += pow(report['avg'], 2)
        count += 1
    # calculate mean
    mean = s1/count
    # (sqrt(count*.s2 - pow(s1, 2)))/count
    stdv = (math.sqrt(count*s2 - pow(s1, 2)))/count
    return (stdv, mean, count, s1, s2)

def calculate_grsf(usage, rack, metricId, reportid, scope, stream, gateway):
    idracdata = IdracData(scope=scope, stream=stream, gateway=gateway)
    stream_info = idracdata.get_stream_start_end()
    grsf = stdv = mean = count = s1= s2 = 0
    # livestream
    read_events = idracdata.get_data_from_idrac_generator_bymetric_id(from_stream_cut=stream_info[0], to_stream_cut=stream_info[1],
                                        data_id=reportid,
                                        rack_label=rack,
                                        metric_id=metricId
                                                                 )
    for _report in read_events:
        s1 += _report['avg']
        s2 += pow(_report['avg'], 2)
        count += 1
        # calculate mean
        mean = s1/count
        stdv = (math.sqrt(count*s2 - pow(s1, 2)))/count 
        grsf = norm.sf(x=usage, loc=mean, scale=stdv) * 100
        _report['grsf'] = grsf
        df = pandas.DataFrame(_report, index=[1])
        df['Timestamp'] = pandas.to_datetime(df['Timestamp'],
                                   format="%Y-%m-%dT%H:%M:%S.%fZ")
        df.set_index('Timestamp', inplace=True)
        yield (df)
    

class IdracData(object):
    def __init__(self, gateway, scope, stream):
        self.scope = scope
        self.stream = stream
        self.gateway = gateway
        self.pravega_client = self.init_pravega_client(gateway)
        self.unindexed_stream = UnindexedStream(self.pravega_client, scope, stream)
        

    def get_events(self, from_stream_cut):
        from_stream_cut = pravega.pb.StreamCut(text=from_stream_cut)
        read_events = self.unindexed_stream.read_events_from_stream(from_stream_cut)
        for i, event in enumerate(read_events):
            yield (dict(event))
        

    def get_stream_start_end(self):
        stream_info = self.unindexed_stream.get_stream_info()
        return (stream_info.head_stream_cut.text, stream_info.tail_stream_cut.text)


    def init_pravega_client(self, gateway):
        pravega_channel = grpc.insecure_channel(gateway, options=[
                                                ('grpc.max_receive_message_length', 9*1024*1024),
        ])
        return pravega.grpc.PravegaGatewayStub(pravega_channel)
   
    def get_metric_from_vsphere(self, from_stream_cut, to_stream_cut=None):
        from_stream_cut = pravega.pb.StreamCut(text=from_stream_cut)
        if to_stream_cut:
            to_stream_cut = pravega.pb.StreamCut(text=to_stream_cut)
        read_events = self.unindexed_stream.read_events_from_stream(from_stream_cut, to_stream_cut)
        for i, event in enumerate(read_events):
            yield(dict(event))

    def get_metric_report_from_idrac(self, from_stream_cut, data_id, rack_label):
        from_stream_cut = pravega.pb.StreamCut(text=from_stream_cut)
        read_events = self.unindexed_stream.read_events_from_stream(from_stream_cut)
        for i, event in enumerate(read_events):
            metric_report = dict(event)
            if metric_report.get('MetricValues') and metric_report.get('Id') == data_id and metric_report.get('RemoteAddr') in racks[rack_label]:
                return metric_report

    def get_data_from_idrac(self, from_stream_cut, data_id, rack_label, metric_id, to_stream_cut=None, count_of_entries=None, interval=None):
        from_stream_cut = pravega.pb.StreamCut(text=from_stream_cut)
        if to_stream_cut:
            to_stream_cut = pravega.pb.StreamCut(text=to_stream_cut)
        read_events = self.unindexed_stream.read_events_from_stream(from_stream_cut, to_stream_cut)
        count = 0
        reports = []
        is_start = True
        record_start_time = None
        endtime = None
        for i, event in enumerate(read_events):
            metric_report = dict(event)
            if metric_report.get('MetricValues') and metric_report.get('Id') == data_id and metric_report.get('RemoteAddr') in racks[rack_label]:
                _report = dict()
                if is_start and interval:
                    record_start_time = time.mktime(datetime.datetime.strptime(metric_report['Timestamp'], "%Y-%m-%dT%H:%M:%S.%fZ").timetuple())
                    local_timezone = tzlocal.get_localzone() # get pytz timezone
                    local_time = datetime.datetime.fromtimestamp(record_start_time, local_timezone)
                    print(local_time)
                    endtime = record_start_time + interval
                    print(datetime.datetime.fromtimestamp(endtime, local_timezone))
                    is_start = False
                sum = 0
                cnt = 0
                for metric in metric_report.get('MetricValues'):
                    if metric.get('MetricId') == metric_id:
                        try:
                            # temporary
                            #if float(metric['MetricValue']) > 20:
                            sum += float(metric['MetricValue'])
                            cnt += 1
                        except:
                            pass
                _report['Timestamp'] = metric_report['Timestamp']
                _report['RemoteAddr'] = metric_report['RemoteAddr']
                _report['Id'] = metric_report['Id']
                _report['MetricId'] = metric_id
                _report['avg'] = sum/cnt
                if count_of_entries:
                    reports.append(_report)
                    count += 1
                    if count >= count_of_entries:
                        break
                elif interval:
                    reports.append(_report)
                    _time = time.mktime(datetime.datetime.strptime(metric_report['Timestamp'], "%Y-%m-%dT%H:%M:%S.%fZ").timetuple())
                    if _time > endtime:
                        print(endtime)
                        break
                elif to_stream_cut:
                    reports.append(_report)
                    
        return reports

    
    
    def get_data_from_idrac_generator_bymetric_id(self, from_stream_cut, data_id, rack_label, metric_id, to_stream_cut=None):
        from_stream_cut = pravega.pb.StreamCut(text=from_stream_cut)
        if to_stream_cut:
            to_stream_cut = pravega.pb.StreamCut(text=to_stream_cut)
        read_events = self.unindexed_stream.read_events_from_stream(from_stream_cut, to_stream_cut)
        for i, event in enumerate(read_events):
            metric_report = dict(event)
            if metric_report.get('MetricValues') and metric_report.get('Id') == data_id and metric_report.get('RemoteAddr') in racks[rack_label]:
                _report = dict()
                sum = 0
                cnt = 0
                for metric in metric_report.get('MetricValues'):
                    if metric.get('MetricId') == metric_id:
                        try:
                            sum += float(metric['MetricValue'])
                            cnt += 1
                        except:
                            pass
                _report['Timestamp'] = metric_report['Timestamp']
                _report['RemoteAddr'] = metric_report['RemoteAddr']
                _report['Id'] = metric_report['Id']
                _report['MetricId'] = metric_id
                _report['avg'] = sum/cnt
                yield _report
                
                
    def get_data_from_idrac_generator_by_id(self, from_stream_cut, data_id, rack_label):
        from_stream_cut = pravega.pb.StreamCut(text=from_stream_cut)
        read_events = self.unindexed_stream.read_events_from_stream(from_stream_cut, None)
        for i, event in enumerate(read_events):
            metric_report = dict(event)
            if metric_report.get('MetricValues') and metric_report.get('Id') == data_id and metric_report.get('RemoteAddr') in racks[rack_label]:
                metrics = dict.fromkeys([i['MetricId'] for i in metric_report.get('MetricValues')])
                metric_ids = collections.defaultdict(list)
                for k in metrics.keys():
                    a = dict()
                    a['sum'] = a['count'] = 0
                    metric_ids[k].append(a)
                for i in metric_report.get('MetricValues'):
                    metric_ids[i['MetricId']][0]['sum'] += float(i['MetricValue'])
                    metric_ids[i['MetricId']][0]['count'] += 1
                for k, v in metric_ids.items():
                    _report = dict()
                    _report['Timestamp'] = metric_report['Timestamp']
                    _report['RemoteAddr'] = metric_report['RemoteAddr']
                    _report['Id'] = metric_report['Id']
                    _report['MetricId'] = k
                    _report['avg'] = v[0]['sum']/v[0]['count']
                    yield _report