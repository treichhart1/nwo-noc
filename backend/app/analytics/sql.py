"""Sanitized PostgreSQL views. No names, addresses, arbitrary labels or raw JSON escape."""

from .catalog import METRICS


def views_sql():
    core = {
        "cpu_percent": "t.cpu_percent",
        "memory_percent": "t.ram_percent",
        "temperature_c": "t.temperature_c",
        "rx_mbps": "t.rx_mbps",
        "tx_mbps": "t.tx_mbps",
        "latency_ms": "t.latency_ms",
        "packet_loss_percent": "t.packet_loss_percent",
        "uptime_seconds": "noc_analytics.number(t.raw_data::jsonb->'mikrotik_v1'->'uptime_seconds',0,315360000)",
        "ospf_full_neighbors": "CASE WHEN jsonb_typeof(t.raw_data::jsonb->'mikrotik_v1'->'ospf_neighbors')='array' AND jsonb_array_length(t.raw_data::jsonb->'mikrotik_v1'->'ospf_neighbors')<=128 THEN (SELECT count(*) FROM jsonb_array_elements(t.raw_data::jsonb->'mikrotik_v1'->'ospf_neighbors') n WHERE lower(n->>'state')='full')::float END",
        "ospf_other_neighbors": "CASE WHEN jsonb_typeof(t.raw_data::jsonb->'mikrotik_v1'->'ospf_neighbors')='array' AND jsonb_array_length(t.raw_data::jsonb->'mikrotik_v1'->'ospf_neighbors')<=128 THEN (SELECT count(*) FROM jsonb_array_elements(t.raw_data::jsonb->'mikrotik_v1'->'ospf_neighbors') n WHERE lower(coalesce(n->>'state','unknown'))<>'full')::float END",
    }
    rf = dict(
        rssi_dbm="signal_dbm",
        remote_rssi_dbm="remote_signal_dbm",
        snr_db="snr_db",
        noise_floor_dbm="noise_floor_dbm",
        rf_capacity_mbps="capacity_mbps",
        rf_tx_mbps="tx_throughput_mbps",
        rf_rx_mbps="rx_throughput_mbps",
        rf_utilization_percent="utilization_percent",
        rf_quality_percent="quality_percent",
        modulation_bits="modulation_bits",
        mcs_index="mcs_index",
        rf_availability_percent="availability_percent",
        frequency_mhz="frequency_mhz",
        channel_width_mhz="bandwidth_mhz",
    )
    tower = {}
    for name, key in [
        ("dc_voltage", "dc_voltage"),
        ("battery_voltage", "battery_voltage"),
        ("battery_runtime_minutes", "reported_runtime_minutes"),
        ("load_watts", "load_watts"),
    ]:
        tower[name] = f"s.data::jsonb->'power'->'{key}'"
    for name, key in [
        ("tower_temperature_c", "temperature_c"),
        ("sector_subscribers", "subscribers"),
        ("sector_utilization_percent", "utilization_percent"),
        ("airtime_percent", "airtime_percent"),
    ]:
        tower[name] = f"s.data::jsonb->'metrics'->'{key}'"
    for name, expr in list(tower.items()):
        low, high = METRICS[name][2:]
        tower[name] = (
            f"CASE WHEN coalesce(s.data::jsonb->'metrics'->>'collector_ok','unknown')='true' AND NOT coalesce(s.data::jsonb->'unavailable','{{}}'::jsonb) ? '{name if name not in ['tower_temperature_c','sector_subscribers','sector_utilization_percent'] else {'tower_temperature_c':'temperature_c','sector_subscribers':'subscribers','sector_utilization_percent':'utilization_percent'}[name]}' THEN noc_analytics.number({expr},{low},{high}) END"
        )
    tower["ac_present"] = (
        "CASE WHEN s.data::jsonb->'metrics'->>'collector_ok'='true' AND NOT coalesce(s.data::jsonb->'unavailable','{}'::jsonb) ? 'ac_present' THEN CASE s.data::jsonb->'power'->'ac_present' WHEN 'true'::jsonb THEN 1.0 WHEN 'false'::jsonb THEN 0.0 END END"
    )
    tower["battery_charging"] = (
        "CASE WHEN s.data::jsonb->'metrics'->>'collector_ok'='true' THEN CASE s.data::jsonb->'power'->>'charge_state' WHEN 'charging' THEN 1.0 WHEN 'discharging' THEN 0.0 WHEN 'idle' THEN 0.0 END END"
    )
    tower["ups_online"] = (
        "CASE WHEN s.data::jsonb->'metrics'->>'collector_ok'='true' THEN CASE s.data::jsonb->'power'->>'ups_state' WHEN 'online' THEN 1.0 WHEN 'on_battery' THEN 0.0 WHEN 'low_battery' THEN 0.0 WHEN 'fault' THEN 0.0 END END"
    )

    def values(fields):
        return ",\n".join(
            f"('{name}', ({expr})::double precision)" for name, expr in fields.items()
        )

    def bounds(fields):
        return ",".join(f"('{n}',{METRICS[n][2]},{METRICS[n][3]})" for n in fields)

    def select_fields(source, fields, family):
        return f"SELECT b.source_id,b.observed_at,b.device_id,b.site_id,b.sector_id,b.link_id,'{family}'::text AS family,b.component,b.mapping_basis,v.metric,CASE WHEN v.value>=limits.low AND v.value<=limits.high THEN v.value END AS value FROM ({source}) b CROSS JOIN LATERAL (VALUES {values(fields)}) v(metric,value) JOIN (VALUES {bounds(fields)}) limits(metric,low,high) ON limits.metric=v.metric"

    # Expressions refer to their native alias inside LATERAL scopes.
    core_sql = f"SELECT t.id AS source_id,t.sampled_at AS observed_at,t.device_id,d.wisp_site_id AS site_id,NULL::integer AS sector_id,NULL::integer AS link_id,'device'::text AS family,'0'::text AS component,'current_inventory'::text AS mapping_basis,v.metric,CASE WHEN v.value>=limits.low AND v.value<=limits.high THEN v.value END AS value FROM public.telemetry_samples t JOIN public.devices d ON d.id=t.device_id CROSS JOIN LATERAL (VALUES {values(core)}) v(metric,value) JOIN (VALUES {bounds(core)}) limits(metric,low,high) ON limits.metric=v.metric"
    rf_fields = {
        name: f"CASE WHEN r.valid THEN r.{col} END" for name, col in rf.items()
    }
    rf_sql = f"SELECT r.id,r.sampled_at,r.device_id,r.site_id,NULL::integer,r.link_id,'rf',md5(r.series_key||':'||r.mapping_digest),'recorded_snapshot',v.metric,CASE WHEN v.value>=limits.low AND v.value<=limits.high THEN v.value END FROM public.rf_analytics_points r CROSS JOIN LATERAL (VALUES {values(rf_fields)}) v(metric,value) JOIN (VALUES {bounds(rf_fields)}) limits(metric,low,high) ON limits.metric=v.metric"
    tower_sql = f"SELECT s.id,s.observed_at,s.device_id,s.site_id,s.sector_id,s.link_id,'tower',md5(s.scope_key),'recorded_snapshot',v.metric,v.value FROM public.tower_health_samples s CROSS JOIN LATERAL (VALUES {values(tower)}) v(metric,value)"
    interface = {
        name: f"noc_analytics.number(i.value->'{key}',0,1e20)"
        for name, key in [
            ("interface_rx_bytes", "rx_bytes"),
            ("interface_tx_bytes", "tx_bytes"),
            ("interface_rx_errors", "rx_errors"),
            ("interface_tx_errors", "tx_errors"),
        ]
    }
    interface["interface_running"] = (
        "CASE i.value->'running' WHEN 'true'::jsonb THEN 1.0 WHEN 'false'::jsonb THEN 0.0 END"
    )
    interface_sql = f"SELECT t.id,t.sampled_at,t.device_id,d.wisp_site_id,NULL::integer,NULL::integer,'interface',md5(coalesce(i.value->>'id',i.ordinality::text)),'current_inventory',v.metric,v.value FROM public.telemetry_samples t JOIN public.devices d ON d.id=t.device_id CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(t.raw_data::jsonb->'mikrotik_v1'->'interfaces')='array' THEN t.raw_data::jsonb->'mikrotik_v1'->'interfaces' ELSE '[]'::jsonb END) WITH ORDINALITY i(value,ordinality) CROSS JOIN LATERAL (VALUES {values(interface)}) v(metric,value) WHERE i.ordinality<=16"
    monitor_sql = "SELECT m.id,m.checked_at,m.device_id,d.wisp_site_id,NULL::integer,NULL::integer,'monitor','0','current_inventory','reachability',CASE m.status WHEN 'up' THEN 1.0 WHEN 'down' THEN 0.0 END FROM public.monitor_checks m JOIN public.devices d ON d.id=m.device_id"
    return (
        """CREATE SCHEMA noc_analytics;
REVOKE ALL ON SCHEMA noc_analytics FROM PUBLIC;
CREATE FUNCTION noc_analytics.number(j jsonb,low double precision,high double precision) RETURNS double precision LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
 SELECT CASE WHEN jsonb_typeof(j)='number' AND length(j::text)<=48 AND j::text ~ '^-?[0-9]{1,24}([.][0-9]{1,16})?([eE][+-]?[0-9]{1,2})?$' THEN CASE WHEN (j::text)::numeric BETWEEN low AND high THEN (j::text)::double precision END END
$$;
CREATE VIEW noc_analytics.metric_points WITH(security_barrier=true) AS
SELECT q.* FROM ("""
        + " UNION ALL ".join([core_sql, rf_sql, tower_sql, interface_sql, monitor_sql])
        + """) q
WHERE q.observed_at >= now()-make_interval(days=>coalesce((SELECT retention_days FROM public.analytics_config WHERE id=1),90)) AND q.observed_at<=now();
CREATE VIEW noc_analytics.hourly WITH(security_barrier=true) AS SELECT bucket,device_id,site_id,sector_id,link_id,family,component,mapping_basis,metric,valid_count,missing_count,value_sum,minimum,maximum,last_observed,projected_at FROM public.analytics_hourly_bins WHERE bucket>=now()-make_interval(days=>coalesce((SELECT retention_days FROM public.analytics_config WHERE id=1),90));
CREATE VIEW noc_analytics.incidents WITH(security_barrier=true) AS SELECT id,site_id,device_id,sector_id,link_id,started_at,coalesce(resolved_at,closed_at) AS restored_at,CASE WHEN status IN ('open','acknowledged','investigating','resolved','closed') THEN status ELSE 'unknown' END AS status,CASE WHEN severity IN ('critical','warning','info','high','medium','low') THEN severity ELSE 'unknown' END AS severity,CASE WHEN customer_impact>=0 THEN customer_impact END AS recorded_customer_impact_estimate FROM public.incidents;
CREATE VIEW noc_analytics.maintenance WITH(security_barrier=true) AS SELECT id,site_id,asset_id,work_order_id,serviced_at FROM public.maintenance_service_records;
CREATE VIEW noc_analytics.equipment WITH(security_barrier=true) AS SELECT a.id AS asset_id,l.site_id,b.device_id,CASE WHEN a.state IN ('installed','in_service','failed','under_maintenance','retired','removed','available') THEN a.state ELSE 'other' END AS state FROM public.equipment_assets a LEFT JOIN public.equipment_locations l ON l.id=a.location_id LEFT JOIN public.equipment_device_bindings b ON b.asset_id=a.id AND b.ended_at IS NULL;
CREATE VIEW noc_analytics.ipam_counts WITH(security_barrier=true) AS SELECT site_id,count(*)::integer AS prefix_count,count(DISTINCT vlan_id)::integer AS vlan_count FROM public.ipam_prefixes GROUP BY site_id;
CREATE VIEW noc_analytics.topology_counts WITH(security_barrier=true) AS SELECT site_id,count(*)::integer AS node_count FROM public.unified_nodes GROUP BY site_id;
CREATE VIEW noc_analytics.calibration WITH(security_barrier=true) AS SELECT id,created_at,job_id,point_index,predicted_dbm,observed_dbm,error_db,CASE WHEN rf_point_id IS NOT NULL THEN 'recorded_rf' ELSE 'operator_survey' END AS observation_kind FROM public.planning_calibrations;
CREATE VIEW noc_analytics.alerts WITH(security_barrier=true) AS SELECT id,device_id,CASE WHEN state IN ('active','cleared') THEN state ELSE 'unknown' END AS status,first_seen_at AS opened_at,cleared_at FROM public.monitoring_alerts;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='noc_app') THEN GRANT USAGE ON SCHEMA noc_analytics TO noc_app; GRANT SELECT ON ALL TABLES IN SCHEMA noc_analytics TO noc_app; END IF; END $$;
"""
    )
