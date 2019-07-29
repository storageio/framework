VOLDRV_DTL_SYNC = 'Synchronous'
VOLDRV_DTL_ASYNC = 'Asynchronous'
VOLDRV_DTL_MANUAL_MODE = 'Manual'
VOLDRV_DTL_AUTOMATIC_MODE = 'Automatic'
VOLDRV_DTL_TRANSPORT_TCP = 'TCP'
VOLDRV_DTL_TRANSPORT_RSOCKET = 'RSocket'

FRAMEWORK_DTL_SYNC = 'sync'
FRAMEWORK_DTL_ASYNC = 'a_sync'
FRAMEWORK_DTL_NO_SYNC = 'no_sync'
FRAMEWORK_DTL_TRANSPORT_TCP = 'tcp'
FRAMEWORK_DTL_TRANSPORT_RSOCKET = 'rdma'

CACHE_BLOCK = 'block_cache'
CACHE_FRAGMENT = 'fragment_cache'

VPOOL_DTL_MODE_MAP = {FRAMEWORK_DTL_SYNC: VOLDRV_DTL_SYNC,
                      FRAMEWORK_DTL_ASYNC: VOLDRV_DTL_ASYNC,
                      FRAMEWORK_DTL_NO_SYNC: None}


# Network config
NETWORK_MAX_NEIGHBOUR_DISTANCE = 9999