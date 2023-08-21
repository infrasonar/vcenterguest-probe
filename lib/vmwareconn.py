import logging
import ssl
from datetime import datetime, timedelta
from pyVmomi import vim, vmodl  # type: ignore
from pyVim import connect

from .asset_cache import AssetCache

MAX_CONN_AGE = 900


def get_by_instance_uuid(ip4, username, password, instance_uuid):
    conn = _get_conn(ip4, username, password)
    content = conn.RetrieveContent()

    search_index = content.searchIndex
    assert len(content.rootFolder.childEntity), 'empty root folder'
    mo = content.rootFolder.childEntity[0]
    return search_index.FindAllByUuid(mo, instance_uuid, True, True)


def get_perf(ip4, username, password, obj_type, metrics, interval):
    conn = _get_conn(ip4, username, password)
    content = conn.RetrieveContent()
    content_time = conn.CurrentTime()
    view_ref = content.viewManager.CreateContainerView(
        container=content.rootFolder, type=[obj_type], recursive=True)

    perf_manager = content.perfManager
    counters_lk = {c.key: c for c in perf_manager.perfCounter}

    results = {}
    for child in view_ref.view:
        available = perf_manager.QueryAvailablePerfMetric(entity=child)

        metric_id = [
            vim.PerformanceManager.MetricId(counterId=m.counterId,
                                            instance=m.instance)
            for m in available
            if m.counterId in counters_lk and (
                counters_lk[m.counterId].groupInfo.key,
                counters_lk[m.counterId].nameInfo.key) in metrics
        ]
        if len(metric_id) == 0:
            # nothing to query
            continue

        end_time = content_time
        start_time = content_time - timedelta(seconds=interval + 1)
        spec = vim.PerformanceManager.QuerySpec(intervalId=20,
                                                entity=child,
                                                metricId=metric_id,
                                                startTime=start_time,
                                                endTime=end_time)
        results[child.config.instanceUuid] = result = {m: {} for m in metrics}
        for stat in perf_manager.QueryStats(querySpec=[spec]):
            for val in stat.value:
                counter = counters_lk[val.id.counterId]
                path = counter.groupInfo.key, counter.nameInfo.key
                instance = val.id.instance
                value = val.value
                result[path][instance] = value

    view_ref.Destroy()
    return results


def drop_connnection(host):
    conn, _ = AssetCache.get_value((host, 'connection'))
    if conn:
        AssetCache.drop((host, 'connection'))
        conn._stub.DropConnections()


def _get_conn(host, username, password):
    conn, expired = AssetCache.get_value((host, 'connection'))
    if expired:
        conn._stub.DropConnections()
    elif conn:
        return conn

    conn = _get_connection(host, username, password)
    if not conn:
        raise ConnectionError('Unable to connect')
    AssetCache.set_value((host, 'connection'), conn, MAX_CONN_AGE)
    return conn


def _get_connection(host, username, password):
    logging.info(f'CONNECTING to {host}')
    context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
    context.verify_mode = ssl.CERT_NONE
    return connect.SmartConnect(
        host=host,
        user=username,
        pwd=password,
        sslContext=context,
        connectionPoolTimeout=10)
