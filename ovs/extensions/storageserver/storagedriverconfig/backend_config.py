# Copyright (C) 2016 iNuron NV
#
# This file is part of Open vStorage Open Source Edition (OSE),
# as available from
#
#      http://www.openvstorage.org and
#      http://www.openvstorage.com.
#
# This file is free software; you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License v3 (GNU AGPLv3)
# as published by the Free Software Foundation, in version 3 as it comes
# in the LICENSE.txt file of the Open vStorage OSE distribution.
#
# Open vStorage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY of any kind.

from ovs.extensions.storageserver.storagedriverconfig.generic_config import GenericConfig


class BackendConfig(GenericConfig):
    """
    Backendinterface container of the storagedriver config
    """
    def __init__(self, local_connection_path=None, backend_connection_pool_capacity=None, backend_interface_retries_on_error=None, backend_interface_retry_interval_secs=None, backend_interface_retry_interval_max_secs=None,
                 backend_connection_pool_blacklist_secs=None, backend_interface_retry_backoff_multiplier=None, backend_interface_partial_read_retries_on_error=None,
                 backend_interface_partial_read_timeout_msecs=None, backend_interface_partial_read_timeout_max_msecs=None, backend_interface_partial_read_timeout_multiplier=None, backend_interface_partial_read_retry_interval_msecs=None,
                 backend_interface_partial_read_retry_interval_max_msecs=None, backend_interface_partial_read_retry_backoff_multiplier=None,  backend_type=None, *args, **kwargs):
        """
        Initiate the volumedriverfs config: backend_interface
        :param local_connection_path: When backend_type is LOCAL: path to use as LOCAL backend, otherwise ignored
        :param backend_connection_pool_capacity: Capacity of the connection pool maintained by the BackendConnectionManager
        :param backend_interface_retries_on_error: How many times to retry a failed backend operation
        :param backend_interface_retry_interval_secs: delay before retrying a failed backend operation in seconds
        :param backend_interface_retry_interval_max_secs:  max delay before retrying a failed backend operation in seconds
        :param backend_connection_pool_blacklist_secs: Duration (in seconds) in which to skip a connection pool after an error
        :param backend_interface_retry_backoff_multiplier: multiplier for the retry interval on each subsequent retry
        :param backend_interface_partial_read_timeout_msecs: timeout for a partial read operation (milliseconds)
        :param backend_interface_partial_read_retries_on_error: How many times to retry a failed partial read operation
        :param backend_interface_partial_read_timeout_max_msecs:  max timeout for a partial read operation on retry (milliseconds)
        :param backend_interface_partial_read_timeout_multiplier: multiplier for the partial read timeout on each subsequent retry
        :param backend_interface_partial_read_retry_interval_msecs: delay before retrying a failed partial read in milliseconds
        :param backend_interface_partial_read_retry_interval_max_msecs: max delay before retrying a failed partial read in milliseconds
        :param backend_interface_partial_read_retry_backoff_multiplier: multiplier for the retry interval on each subsequent retry (< 0 -> backend_interface_retry_backoff_multiplier is used)

        :param backend_type: Type of backend connection one of ALBA, LOCAL, MULTI or S3, the other parameters in this section are only used when their correct backendtype is set

        """
        self.backend_type = backend_type
        if self.backend_type == 'LOCAL' and local_connection_path is None:
            raise RuntimeError('Local_connection_path needs to be provided if backendtype is LOCAL')

        self.local_connection_path = local_connection_path

        self.backend_connection_pool_capacity = backend_connection_pool_capacity
        self.backend_interface_retries_on_error = backend_interface_retries_on_error
        self.backend_interface_retry_interval_secs = backend_interface_retry_interval_secs
        self.backend_connection_pool_blacklist_secs = backend_connection_pool_blacklist_secs
        self.backend_interface_retry_interval_max_secs = backend_interface_retry_interval_secs
        self.backend_interface_retry_interval_max_secs = backend_interface_retry_interval_max_secs
        self.backend_interface_retry_backoff_multiplier = backend_interface_retry_backoff_multiplier
        self.backend_interface_partial_read_timeout_msecs = backend_interface_partial_read_timeout_msecs
        self.backend_interface_partial_read_retries_on_error = backend_interface_partial_read_retries_on_error
        self.backend_interface_partial_read_timeout_max_msecs = backend_interface_partial_read_timeout_max_msecs
        self.backend_interface_partial_read_timeout_multiplier = backend_interface_partial_read_timeout_multiplier
        self.backend_interface_partial_read_retry_interval_msecs = backend_interface_partial_read_retry_interval_msecs
        self.backend_interface_partial_read_retry_interval_max_msecs = backend_interface_partial_read_retry_interval_max_msecs
        self.backend_interface_partial_read_retry_backoff_multiplier = backend_interface_partial_read_retry_backoff_multiplier

        self.alba_connection_config = AlbaConnectionConfig(**kwargs)

        self._nr_of_proxies = 1

    def set_nr_of_proxies(self, nr_of_proxies):
        if nr_of_proxies is isinstance(int, nr_of_proxies) and nr_of_proxies < 0:
            self._nr_of_proxies = nr_of_proxies


    def get_config(self):
        # Assign Alba connection configs per proxy to the backend config
        fixed_config = self.alba_connection_config.get_config()
        fixed_config['local_connection_path'] = self.local_connection_path
        tmp_dict = dict([(i, fixed_config) for i in xrange(self._nr_of_proxies)])

        # Assign other config keys to the backend config
        to_add = vars(self).copy()
        to_add.pop('_nr_of_proxies')
        to_add.pop('alba_connection_config')
        to_add.pop('local_connection_path')
        tmp_dict.update(to_add)
        tmp_dict['backend_type'] = 'MULTI'

        return tmp_dict


class AlbaConnectionConfig(GenericConfig):
    def __init__(self, alba_connection_host=None, alba_connection_port=None, alba_connection_preset=None, alba_connection_timeout=None, alba_connection_use_rora=None,
                 alba_connection_transport=None, alba_connection_rora_timeout_msecs=None, alba_connection_rora_manifest_cache_capacity=None, alba_connection_asd_connection_pool_capacity=None,
                 *args, **kwargs):
        """
        :param alba_connection_host: When backend_type is ALBA: the ALBA host to connect to, otherwise ignored
        :param alba_connection_port: When backend_type is ALBA: The ALBA port to connect to, otherwise ignored
        :param alba_connection_preset: When backend_type is ALBA: the ALBA preset to use for new namespaces
        :param alba_connection_timeout: The timeout for the ALBA proxy, in seconds
        :param alba_connection_use_rora: Whether to enable Read Optimized RDMA ASD (RORA) support
        :param alba_connection_transport: When backend_type is ALBA: the ALBA connection to use: TCP (default) or RDMA
        :param alba_connection_rora_timeout_msecs: Timeout for RORA (fast path) partial reads (milliseconds)
        :param alba_connection_rora_manifest_cache_capacity: Capacity of the RORA fetcher's manifest cache
        :param alba_connection_asd_connection_pool_capacity: connection pool (per ASD) capacity
        """
        self.alba_connection_host = alba_connection_host
        self.alba_connection_port = alba_connection_port
        self.alba_connection_preset = alba_connection_preset
        self.alba_connection_timeout = alba_connection_timeout
        self.alba_connection_use_rora = alba_connection_use_rora
        self.alba_connection_transport = alba_connection_transport
        self.alba_connection_rora_timeout_msecs = alba_connection_rora_timeout_msecs
        self.alba_connection_rora_manifest_cache_capacity = alba_connection_rora_manifest_cache_capacity
        self.alba_connection_asd_connection_pool_capacity = alba_connection_asd_connection_pool_capacity