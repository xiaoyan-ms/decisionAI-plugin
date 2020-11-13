import json
import math
import datetime

from .util.constant import STATUS_SUCCESS, STATUS_FAIL
from .util.retryrequests import RetryRequests
from .util.series import Series
from .util.timeutil import get_time_offset, str_to_dt, dt_to_str, get_time_list

from telemetry import log

REQUEST_TIMEOUT_SECONDS = 300


def get_field_idx(fields, target):
    for idx, field in enumerate(fields):
        if field == '__FIELD__.' + target:
            return idx

    raise Exception("Not found field {} in {}".format(target, ','.join(fields)))


class TSANAClient(object):
    def __init__(self, series_limit, username=None, password=None, retrycount=3, retryinterval=1000):
        self.series_limit = series_limit
        self.username = username
        self.password = password
        self.retryrequests = RetryRequests(retrycount, retryinterval)

    def post(self, api_endpoint, api_key, path, data):
        url = api_endpoint.rstrip('/') + path
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }

        if self.username and self.password:
            auth = (self.username, self.password)
        else:
            auth = None

        try:
            r = self.retryrequests.post(url=url, headers=headers, auth=auth, data=json.dumps(data),
                                        timeout=REQUEST_TIMEOUT_SECONDS, verify=False)
            if r.status_code != 204:
                return r.json()
        except Exception as e:
            raise Exception('TSANA service api "{}" failed, request:{}, {}'.format(path, data, str(e)))

    def put(self, api_endpoint, api_key, path, data):
        url = api_endpoint.rstrip('/') + path
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }

        if self.username and self.password:
            auth = (self.username, self.password)
        else:
            auth = None

        try:
            r = self.retryrequests.put(url=url, headers=headers, auth=auth, data=json.dumps(data),
                                        timeout=REQUEST_TIMEOUT_SECONDS, verify=False)
            if r.status_code != 204:
                return r.json()
        except Exception as e:
            raise Exception('TSANA service api "{}" failed, request:{}, {}'.format(path, data, str(e)))

    def get(self, api_endpoint, api_key, path):
        url = api_endpoint.rstrip('/') + path
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }

        if self.username and self.password:
            auth = (self.username, self.password)
        else:
            auth = None

        try:
            r = self.retryrequests.get(url=url, headers=headers, auth=auth, timeout=REQUEST_TIMEOUT_SECONDS,
                                       verify=False)
            return r.json()
        except Exception as e:
            raise Exception('TSANA service api "{}" failed, {}'.format(path, str(e)))

    def get_group_detail(self, api_endpoint, api_key, group_id):
        return self.get(api_endpoint, api_key, '/timeSeriesGroups/' + group_id)

    # To get the meta of a specific metric from TSANA
    # Parameters:
    #   apiEndpoint: api endpoint for specific user
    #   apiKey: api key for specific user
    #   metric_id: a UUID string
    # Return:
    #   meta: the meta of the specified metric, or None if there is something wrong. 
    def get_metric_meta(self, api_endpoint, api_key, metric_id):
        r = self.get(api_endpoint, api_key, '/metrics/' + metric_id + '/meta')
        log.info("******get_metric_meta******:\nresponse: {}\n******************".format(r))
        return r

    def get_dimesion_values(self, api_endpoint, api_key, metric_id, dimension_name):
        dims = self.get(api_endpoint, api_key, '/metrics/' + metric_id + '/dimensions')
        if 'dimensions' in dims and dimension_name in dims['dimensions']:
            return dims['dimensions'][dimension_name]
        else:
            return None

    # Query time series from TSANA
    # Parameters:
    #   apiEndpoint: api endpoint for specific user
    #   apiKey: api key for specific user
    #   series_sets: Array of series set
    #   start_time: inclusive, the first timestamp to be query
    #   end_time: exclusive
    #   offset: a number will be added to each timestamp of each time-series. The unit is defined by granularity
    #   granularityName: if Offset > 0, the granularityName is Monthly / Weekly / Daily / Hourly / Minutely / Secondly / Custom
    #   granularityAmount: if granularityName is Custom, granularityAmount is the seconds of the exact granularity
    # Return: 
    #   A array of Series object
    def get_timeseries(self, api_endpoint, api_key, series_sets, start_time, end_time, offset=0, top=20, fields_filter=[]):
        end_str = dt_to_str(end_time)
        start_str = dt_to_str(start_time)

        multi_series_data = []
        # Query each series's tag
        for data in series_sets:
            dim = {}
            if 'dimensionFilter' not in data:
                data['dimensionFilter'] = data['filters']

            for dimkey in data['dimensionFilter']:
                dim[dimkey] = [data['dimensionFilter'][dimkey]]

            skip = 0
            count = 0
            para = dict(metricId=data['metricId'], dimensionFilter=dim, activeSince=start_str)
            while True:
                # Max series limit per call is 1k
                ret = self.post(api_endpoint, api_key, '/metrics/' + data['metricId'] + '/series/query?$skip={}&$top={}'.format(skip, 1000), data=para)
                if len(ret['value']) == 0:
                    break

                series = []
                for s in ret['value']:
                    s['startTime'] = start_str
                    s['endTime'] = end_str
                    s['returnSeriesId'] = True
                    series.append(s)
                
                if len(series) > 0:
                    granularityName = data['metricMeta']['granularityName']
                    granularityAmount = data['metricMeta']['granularityAmount']
                    log.info("******get data begin for {}******".format(data))
                    ret = self.post(api_endpoint, api_key, '/metrics/series/data', data=dict(value=series))
                    log.info("******get data end for {}******".format(data))

                    sub_multi_series_data = []
                    for factor in ret['value']:
                        if len(factor['values']) <= 0:
                            continue

                        log.info("******new series {} in {}******".format(factor['id']['seriesId'], data))
                        sub_multi_series_data.append(
                            Series(factor['id']['metricId'], factor['id']['seriesId'], factor['id']['dimension'],
                                [dict(timestamp=dt_to_str(get_time_offset(str_to_dt(y[0]), (granularityName, granularityAmount), offset)), 
                                        value=y[1], **{field: y[get_field_idx(factor['fields'], field)] for field in fields_filter})
                                for y in factor['values']]))
                    
                    count = count + len(sub_multi_series_data)
                    multi_series_data.extend(sub_multi_series_data)

                    if count >= top:
                        break

                skip = skip + len(ret['value'])
                if skip >= 200000:
                    break

        if not len(multi_series_data):
            raise Exception("Series is empty")
        
        # dump data
        '''
        data_str = ''
        for series in multi_series_data:
            data_str += json.dumps(series.__dict__) + '\n'
        log.info("******get_timeseries******:\nresponse: {}\n******************".format(data_str))
        '''
        return multi_series_data

    # Get ranked dimensions
    # Parameters:
    #   api_endpoint: api endpoint for specific user
    #   api_key: api key for specific user
    #   metric_id: uuid for metric
    #   dimensions: included dimensions
    #   start_time: inclusive, the first timestamp to be query
    #   top: max count for returned results
    #   skip: offset
    # Return:
    #   ranked series dimensions
    def rank_series(self, api_endpoint, api_key, metric_id, dimensions, start_time, top=10, skip=0):
        url = f'/metrics/{metric_id}/rank-series'
        para = dict(dimensions=dimensions, count=top, startTime=start_time, skip=skip)
        return self.post(api_endpoint, api_key, url, data=para)

    # Save a training result back to TSANA
    # Parameters: 
    #   parameters: a dict object which should includes
    #       apiEndpoint: api endpoint for specific user
    #       apiKey: api key for specific user
    #       groupId: groupId in TSANA, which is copied from inference request, or from the entity
    #       instance: instance object, which is copied from the inference request, or from the entity
    #   model_id: model id
    #   model_state: model state(TRAINING,READY,FAILED,DELETED)
    #   message: detail message
    # Return:
    #   result: STATE_SUCCESS / STATE_FAIL
    #   message: description for the result 
    def save_training_result(self, parameters, model_id, model_state:str, message:str):
        try:
            body = {
                'modelId': model_id, 
                'state': model_state, 
                'message': message
            }

            self.put(parameters['apiEndpoint'], parameters['apiKey'], '/timeSeriesGroups/' + parameters['groupId'] + '/appInstances/' + parameters['instance']['instanceId'] + '/modelKey', body)
            return STATUS_SUCCESS, ''
        except Exception as e:
            return STATUS_FAIL, str(e)

    # Save a inference result back to TSANA
    # Parameters: 
    #   parameters: a dict object which should includes
    #       apiEndpoint: api endpoint for specific user
    #       apiKey: api key for specific user
    #       groupId: groupId in TSANA, which is copied from inference request, or from the entity
    #       instance: instance object, which is copied from the inference request, or from the entity
    #   result: an array of inference result. 
    # Return:
    #   result: STATE_SUCCESS / STATE_FAIL
    #   message: description for the result
    def save_inference_result(self, parameters, result, batch_size=1000):
        try: 

            if len(result) <= 0: 
                return STATUS_SUCCESS, ''

            for batch_index in range(math.ceil(len(result) / batch_size)):
                body = {
                'groupId': parameters['groupId'], 
                'instanceId': parameters['instance']['instanceId'], 
                'results': []
                }
                batch_start = batch_index * batch_size
                for step in range(min(batch_size, len(result) - batch_start)):
                    item = result[batch_start + step]
                    item['timestamp'] = dt_to_str(str_to_dt(item['timestamp']))
                    body['results'].append({
                        'params': parameters['instance']['params'],
                        'timestamp': item['timestamp'],
                        'result': item,
                        'status': item['status']
                    })
                self.post(parameters['apiEndpoint'], parameters['apiKey'], '/timeSeriesGroups/' + parameters['groupId'] + '/appInstances/' + parameters['instance']['instanceId'] + '/saveResult', body)
            return STATUS_SUCCESS, ''
        except Exception as e:
            return STATUS_FAIL, str(e)

    def get_inference_result(self, parameters):
        try: 
            ret = self.get(parameters['apiEndpoint'], parameters['apiKey'], '/timeSeriesGroups/' 
                                + parameters['groupId'] 
                                + '/appInstances/' 
                                + parameters['instance']['instanceId'] 
                                + '/history?startTime=' 
                                + parameters['startTime']
                                + '&endTime=' 
                                + parameters['endTime'])
            
            return STATUS_SUCCESS, '', ret
        except Exception as e:
            return STATUS_FAIL, str(e), None

    # Save a inference result back to TSANA
    # Parameters: 
    #   parameters: a dict object which should includes
    #       apiEndpoint: api endpoint for specific user
    #       apiKey: api key for specific user
    #       groupId: groupId in TSANA, which is copied from inference request, or from the entity
    #       instance: instance object, which is copied from the inference request, or from the entity
    #   metric_id: a UUID string
    #   dimensions: a dict includes dimension name and value
    #   timestamps: an array of timestamps
    #   values: an array of inference result values
    # Return:
    #   result: STATE_SUCCESS / STATE_FAIL
    #   message: description for the result
    def save_data_points(self, parameters, metricId, dimensions, timestamps, values):
        try: 
            if len(values) <= 0: 
                return STATUS_SUCCESS, ''

            body = {
                "metricId": metricId, 
                "dimensions": dimensions,
                "timestamps": timestamps, 
                "values": values
            }
            print(json.dumps(body))

            self.post(parameters['apiEndpoint'], parameters['apiKey'], '/pushData', body)
            return STATUS_SUCCESS, ''
        except Exception as e:
            return STATUS_FAIL, str(e)

    # Push alert in general
    # Parameters:
    #   api_endpoint: api endpoint for specific user
    #   api_key: api key for specific user
    #   alert_type: alert type
    #   message: alert message
    # Return:
    #   result: STATE_SUCCESS / STATE_FAIL 
    #   message: description for the result
    def push_alert(self, api_endpoint, api_key, alert_type, message):
        try:
            url = '/timeSeriesGroups/alert'
            para = dict(alertType=alert_type, message=message)
            self.post(api_endpoint, api_key, url, data=para)
            return STATUS_SUCCESS, ''
        except Exception as e:
            return STATUS_FAIL, repr(e)