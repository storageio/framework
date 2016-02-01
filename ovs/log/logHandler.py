# Copyright 2014 iNuron NV
#
# Licensed under the Open vStorage Modified Apache License (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.openvstorage.org/license
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Contains the loghandler module
"""

import os
import sys
import time
import inspect
import socket
import logging
import itertools


class OVSFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        """
        Overrides the default formatter to include UTC offset
        """
        _ = datefmt
        ct = self.converter(record.created)
        tz = time.altzone if time.daylight and ct.tm_isdst > 0 else time.timezone
        offset = '{0}{1:0>2}{2:0>2}'.format('-' if tz > 0 else '+', abs(tz) // 3600, abs(tz // 60) % 60)
        base_time = time.strftime('%Y-%m-%d %H:%M:%S', ct)
        return '{0} {1:05.0f} {2}'.format(base_time, record.msecs, offset)

    def format(self, record):
        if 'hostname' not in record.__dict__:
            record.hostname = socket.gethostname()
        if 'sequence' not in record.__dict__:
            record.sequence = LogHandler.counter.next()
        return super(OVSFormatter, self).format(record)


class LogHandler(object):
    """
    Log handler.

    WARNING: This log handler might be highly unreliable if not used correctly. It can log to redis, but if Redis is
    not working as expected, it will result in lost log messages. If you want reliable logging, do not use Redis at all
    or log to files and have a separate process forward them to Redis (so logs can be re-send if Redis is unavailable)
    """

    counter = itertools.count()
    cache = {}
    propagate_cache = {}
    targets = {'lib': 'lib',
               'api': 'api',
               'extensions': 'extensions',
               'dal': 'dal',
               'celery': 'celery',
               'arakoon': 'arakoon',
               'support': 'support',
               'log': 'audit_trails',
               'storagerouterclient': 'storagerouterclient'}

    def __init__(self, source, name=None, propagate=True):
        """
        Initializes the logger
        """
        parent_invoker = inspect.stack()[1]
        if not __file__.startswith(parent_invoker[1]) or parent_invoker[3] != 'get':
            raise RuntimeError('Cannot invoke instance from outside this class. Please use LogHandler.get(source, name=None) instead')

        if name is None:
            name = 'logger'

        formatter = OVSFormatter('%(asctime)s - %(hostname)s - %(process)s/%(thread)d - {0}/%(name)s - %(sequence)s - %(levelname)s - %(message)s'.format(source))

        target_definition = LogHandler.load_target_definition(source, name)
        if target_definition['type'] == 'redis':
            from redis import Redis
            from ovs.log.redis_logging import RedisListHandler
            self.handler = RedisListHandler(queue=target_definition['queue'],
                                            client=Redis(host=target_definition['host'],
                                                         port=target_definition['port']))
        elif target_definition['type'] == 'file':
            self.handler = logging.FileHandler(target_definition['filename'])
        else:
            self.handler = logging.StreamHandler(sys.stdout)
        self.handler.setFormatter(formatter)
        self.logger = logging.getLogger(name)
        self.logger.addHandler(self.handler)
        self.logger.propagate = propagate
        self.logger.setLevel(getattr(logging, 'DEBUG'))
        self._key = '{0}_{1}'.format(source, name)

    @staticmethod
    def load_target_definition(source, name=None):
        if name is None:
            name = 'logger'

        logging_target = {'type': 'stdout'}
        try:
            from ovs.extensions.db.etcd.configuration import EtcdConfiguration
            logging_target = EtcdConfiguration.get('/ovs/framework/logging')
        except:
            pass

        target_type = logging_target['type']
        if 'OVS_LOGTYPE_OVERRIDE' in os.environ:
            target_type = os.environ['OVS_LOGTYPE_OVERRIDE']

        if target_type == 'redis':
            queue = logging_target.get('queue', 'ovs_logging')
            if '{0}' in queue:
                queue = queue.format(name)
            return {'type': 'redis',
                    'queue': queue,
                    'host': logging_target.get('host', 'localhost'),
                    'port': logging_target.get('port', 6379)}
        if target_type == 'file':
            return {'type': 'file',
                    'filename': LogHandler.load_path(source)}
        return {'type': 'stdout'}

    @staticmethod
    def load_path(source):
        log_filename = '/var/log/ovs/{0}.log'.format(
            LogHandler.targets[source] if source in LogHandler.targets else 'generic'
        )
        if not os.path.exists(log_filename):
            open(log_filename, 'a').close()
            os.chmod(log_filename, 0o666)
        return log_filename

    @staticmethod
    def get(source, name=None, propagate=True):
        key = '{0}_{1}'.format(source, name)
        if key not in LogHandler.cache:
            logger = LogHandler(source, name, propagate)
            LogHandler.cache[key] = logger
        if key not in LogHandler.propagate_cache:
            LogHandler.propagate_cache[key] = propagate
        return LogHandler.cache[key]

    def _fix_propagate(self):
        """
        Obey propagate flag as initially called
        - celery will overwrite it to catch the logging
        """
        propagate = LogHandler.propagate_cache.get(self._key, None)
        if propagate is not None:
            self.logger.propagate = propagate

    def info(self, msg, *args, **kwargs):
        """ Info """
        self._fix_propagate()
        if 'print_msg' in kwargs:
            del kwargs['print_msg']
            print msg
        extra = kwargs.get('extra', {})
        extra['hostname'] = socket.gethostname()
        extra['sequence'] = LogHandler.counter.next()
        kwargs['extra'] = extra
        try:
            return self.logger.info(msg, *args, **kwargs)
        except:
            pass

    def error(self, msg, *args, **kwargs):
        """ Error """
        self._fix_propagate()
        if 'print_msg' in kwargs:
            del kwargs['print_msg']
            print msg
        extra = kwargs.get('extra', {})
        extra['hostname'] = socket.gethostname()
        extra['sequence'] = LogHandler.counter.next()
        kwargs['extra'] = extra
        try:
            return self.logger.error(msg, *args, **kwargs)
        except:
            pass

    def debug(self, msg, *args, **kwargs):
        """ Debug """
        self._fix_propagate()
        if 'print_msg' in kwargs:
            del kwargs['print_msg']
            print msg
        extra = kwargs.get('extra', {})
        extra['hostname'] = socket.gethostname()
        extra['sequence'] = LogHandler.counter.next()
        kwargs['extra'] = extra
        try:
            return self.logger.debug(msg, *args, **kwargs)
        except:
            pass

    def warning(self, msg, *args, **kwargs):
        """ Warning """
        self._fix_propagate()
        if 'print_msg' in kwargs:
            del kwargs['print_msg']
            print msg
        extra = kwargs.get('extra', {})
        extra['hostname'] = socket.gethostname()
        extra['sequence'] = LogHandler.counter.next()
        kwargs['extra'] = extra
        try:
            return self.logger.warning(msg, *args, **kwargs)
        except:
            pass

    def log(self, msg, *args, **kwargs):
        """ Log """
        self._fix_propagate()
        if 'print_msg' in kwargs:
            del kwargs['print_msg']
            print msg
        extra = kwargs.get('extra', {})
        extra['hostname'] = socket.gethostname()
        extra['sequence'] = LogHandler.counter.next()
        kwargs['extra'] = extra
        try:
            return self.logger.log(msg, *args, **kwargs)
        except:
            pass

    def critical(self, msg, *args, **kwargs):
        """ Critical """
        self._fix_propagate()
        if 'print_msg' in kwargs:
            del kwargs['print_msg']
            print msg
        extra = kwargs.get('extra', {})
        extra['hostname'] = socket.gethostname()
        extra['sequence'] = LogHandler.counter.next()
        kwargs['extra'] = extra
        try:
            return self.logger.critical(msg, *args, **kwargs)
        except:
            pass

    def exception(self, msg, *args, **kwargs):
        """ Exception """
        self._fix_propagate()
        if 'print_msg' in kwargs:
            del kwargs['print_msg']
            print msg
        extra = kwargs.get('extra', {})
        extra['hostname'] = socket.gethostname()
        extra['sequence'] = LogHandler.counter.next()
        kwargs['extra'] = extra
        try:
            return self.logger.exception(msg, *args, **kwargs)
        except:
            pass
