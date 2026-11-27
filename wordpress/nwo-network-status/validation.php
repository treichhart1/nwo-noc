<?php
// SPDX-License-Identifier: GPL-2.0-or-later
if (!defined('ABSPATH')) { exit; }
final class NWO_Status_Validation {
    public const LABELS = ['unknown'=>'Status unknown','all_systems_operational'=>'All systems operational','investigating'=>'Investigating','service_disruption'=>'Service disruption','partial_service_interruption'=>'Partial service interruption','major_service_interruption'=>'Major service interruption','degraded_performance'=>'Degraded performance','scheduled_maintenance'=>'Scheduled maintenance','maintenance_underway'=>'Maintenance in progress','monitoring_recovery'=>'Monitoring recovery','resolved'=>'Resolved'];
    public static function fail(): never { throw new RuntimeException('invalid_public_contract'); }
    public static function keys(array $value, array $keys): void { if (array_diff(array_keys($value), $keys)) { self::fail(); } }
    public static function prose(mixed $v, int $max, int $min=0): string {
        if (!is_string($v) || strlen($v)>$max*4 || mb_strlen($v)>$max || mb_strlen($v)<$min || preg_match('/[<>@]|https?:\/\/|\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[0-9a-f]{1,4}:){2}|\+?\d{10,15}|\d{3}[-. ]\d{3}[-. ]\d{4}|[-+]?\d{1,3}\.\d{4,}|[\x00-\x08\x0b-\x1f\x7f]/iu',$v)) { self::fail(); }
        return $v;
    }
    public static function date(mixed $v): int {
        if (!is_string($v) || strlen($v)>40 || !preg_match('/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)$/D',$v)) { self::fail(); }
        $date=strtotime($v); $parts=date_parse($v); if ($date===false || $parts['warning_count'] || $parts['error_count']) { self::fail(); } return $date;
    }
    public static function uuid(mixed $v): void { if (!is_string($v) || !preg_match('/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/D',$v)) { self::fail(); } }
    public static function status(mixed $v): void { if (!is_string($v) || !isset(self::LABELS[$v])) { self::fail(); } }
    public static function area(array $a): void {
        self::keys($a,['id','label','county','technology','group_label','description','status']); self::uuid($a['id']??null); self::prose($a['label']??null,100,3); self::status($a['status']??null);
        if (!in_array($a['technology']??null,['unknown','wireless','fiber','hybrid','regional'],true)) { self::fail(); }
        foreach (['county'=>100,'group_label'=>100,'description'=>200] as $key=>$max) { if (isset($a[$key])) { self::prose($a[$key],$max); } }
    }
    public static function feed(string $body, ?int $now=null): array {
        if (strlen($body)>524288) { self::fail(); } $now??=time();
        $d=json_decode($body,true,32,JSON_THROW_ON_ERROR); if (!is_array($d)) { self::fail(); }
        self::keys($d,['api_version','overall_status','freshness','generated_at','expires_at','last_public_update','basis','service_areas','incidents','maintenance','resolved','next_history_offset','revision_token']);
        if (($d['api_version']??null)!=='2' || ($d['basis']??null)!=='administrator-approved announcements; not live service verification' || !in_array($d['freshness']??null,['fresh','stale'],true) || !is_string($d['revision_token']??null) || !preg_match('/^[a-f0-9]{64}$/D',$d['revision_token'])) { self::fail(); }
        self::status($d['overall_status']??null);
        $generated=self::date($d['generated_at']??null); $expires=self::date($d['expires_at']??null); $updated=self::date($d['last_public_update']??null);
        if ($generated>$now+60 || $generated<$now-120 || $expires<=$now || $expires>$generated+120 || $expires<=$generated || $updated>$now+60 || ($d['freshness']==='fresh' && $now-$updated>86400) || ($d['freshness']==='stale' && $d['overall_status']!=='unknown')) { self::fail(); }
        $seen=[];
        foreach (['incidents','maintenance','resolved'] as $group) {
            if (!isset($d[$group]) || !is_array($d[$group]) || !array_is_list($d[$group]) || count($d[$group])>200) { self::fail(); }
            foreach ($d[$group] as $item) {
                if (!is_array($item)) { self::fail(); }
                self::keys($item,['id','title','description','status','areas','starts_at','ends_at','estimated_restoration_at','updated_at','impact','area_details','display_status']); self::uuid($item['id']??null);
                if (isset($seen[$item['id']])) { self::fail(); } $seen[$item['id']]=true;
                self::prose($item['title']??null,120,5); self::prose($item['description']??null,1200,10); self::status($item['status']??null); self::status($item['display_status']??null); self::date($item['updated_at']??null);
                if (!in_array($item['impact']??null,['general','partial','major','degraded'],true) || !isset($item['areas']) || !is_array($item['areas']) || !array_is_list($item['areas']) || count($item['areas'])>20 || !is_array($item['area_details']??null) || count($item['area_details'])>20) { self::fail(); }
                foreach ($item['areas'] as $area) { self::prose($area,100,1); }
                foreach ($item['area_details'] as $area) { if (!is_array($area)) { self::fail(); } self::area($area); }
                foreach (['starts_at','ends_at','estimated_restoration_at'] as $key) { if (isset($item[$key])) { self::date($item[$key]); } }
            }
        }
        if (!is_array($d['service_areas']??null) || !array_is_list($d['service_areas']) || count($d['service_areas'])>200) { self::fail(); }
        foreach ($d['service_areas'] as $area) { if (!is_array($area)) { self::fail(); } self::area($area); if ($d['freshness']==='stale' && $area['status']!=='unknown') { self::fail(); } }
        if (($d['next_history_offset']??null)!==null && (!is_int($d['next_history_offset']) || $d['next_history_offset']<0 || $d['next_history_offset']>10000)) { self::fail(); }
        return $d;
    }
}
