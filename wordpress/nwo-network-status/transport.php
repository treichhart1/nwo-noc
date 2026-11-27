<?php
// SPDX-License-Identifier: GPL-2.0-or-later
if (!defined('ABSPATH')) { exit; }
final class NWO_Status_Transport {
    private static function cidr(string $ip,string $range): bool {
        [$net,$bits]=explode('/',$range); $a=inet_pton($ip); $b=inet_pton($net); if ($a===false || $b===false || strlen($a)!==strlen($b)) { return false; }
        $whole=intdiv((int)$bits,8);$part=(int)$bits%8;
        if (substr($a,0,$whole)!==substr($b,0,$whole)) { return false; }
        if ($part===0) { return true; }
        $mask=(255 << (8-$part)) & 255;
        return (ord($a[$whole]) & $mask)===(ord($b[$whole]) & $mask);
    }
    public static function public_ip(string $ip): bool {
        if (!filter_var($ip,FILTER_VALIDATE_IP,FILTER_FLAG_NO_PRIV_RANGE|FILTER_FLAG_NO_RES_RANGE)) { return false; }
        $blocked=['0.0.0.0/8','10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16','172.16.0.0/12','192.0.0.0/24','192.0.2.0/24','192.88.99.0/24','192.168.0.0/16','198.18.0.0/15','198.51.100.0/24','203.0.113.0/24','224.0.0.0/4','240.0.0.0/4','2001::/23','2001:db8::/32','2002::/16','3fff::/20'];
        if (str_contains($ip,':') && !self::cidr($ip,'2000::/3')) { return false; }
        foreach ($blocked as $range) { if (self::cidr($ip,$range)) { return false; } } return true;
    }
    public static function endpoint(string $url): array {
        if (strlen($url)>255 || preg_match('/[\x00-\x20\\\\%]/',$url)) { throw new RuntimeException('gateway_url_rejected'); }
        $u=parse_url($url); $host=strtolower($u['host']??'');
        if (!$u || ($u['scheme']??'')!=='https' || isset($u['user']) || isset($u['pass']) || isset($u['query']) || isset($u['fragment']) || (isset($u['port']) && $u['port']!==443) || !in_array($u['path']??'',['/public/network-status/v2','/network-status-api/v2'],true) || !preg_match('/^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/D',$host) || !preg_match('/[a-z]/',$host) || preg_match('/(?:^|\.)(localhost|local|internal|lan|home|arpa)$/D',$host) || filter_var($host,FILTER_VALIDATE_IP)) { throw new RuntimeException('gateway_url_rejected'); }
        return ['url'=>'https://'.$host.$u['path'],'host'=>$host];
    }
    public static function resolve(string $host): array {
        // Only administrator configuration resolves DNS. Runtime reads use the
        // approved public addresses pinned below, preventing DNS rebinding.
        $records=dns_get_record($host,DNS_A|DNS_AAAA); $addresses=[];
        if ($records===false) { throw new RuntimeException('gateway_dns_unavailable'); }
        foreach ($records as $r) { $ip=$r['ip']??$r['ipv6']??null; if ($ip!==null) { $addresses[]=$ip; } }
        if (!$addresses || count($addresses)>16) { throw new RuntimeException('gateway_dns_unavailable'); }
        foreach ($addresses as $ip) { if (!self::public_ip($ip)) { throw new RuntimeException('gateway_private_destination_rejected'); } }
        return array_values(array_unique($addresses));
    }
    public static function fetch(array $settings,int $offset=0): array {
        if (!function_exists('curl_init') || !defined('CURLOPT_RESOLVE')) { throw new RuntimeException('curl_tls_required'); }
        if (empty($settings['enabled']) || $offset<0 || $offset>180 || $offset%20!==0) { throw new RuntimeException('gateway_not_configured'); }
        $endpoint=self::endpoint($settings['gateway_url']??''); $addresses=$settings['gateway_ips']??[];
        if (!is_array($addresses) || !$addresses || count($addresses)>16) { throw new RuntimeException('gateway_not_configured'); }
        foreach ($addresses as $ip) { if (!is_string($ip) || !self::public_ip($ip)) { throw new RuntimeException('gateway_private_destination_rejected'); } }
        if (class_exists('WP_Http') && (new WP_Http())->block_request($endpoint['url'])) { throw new RuntimeException('wordpress_egress_policy_blocked'); }
        $ip=$addresses[0]; $target=str_contains($ip,':')?'['.$ip.']':$ip; $body='';$headers=[];$h=curl_init();
        try {
            curl_setopt_array($h,[CURLOPT_URL=>$endpoint['url'].'?history_offset='.$offset.'&history_limit=20',CURLOPT_HTTPGET=>true,CURLOPT_FOLLOWLOCATION=>false,CURLOPT_MAXREDIRS=>0,CURLOPT_PROTOCOLS=>CURLPROTO_HTTPS,CURLOPT_REDIR_PROTOCOLS=>CURLPROTO_HTTPS,CURLOPT_CONNECTTIMEOUT_MS=>2000,CURLOPT_TIMEOUT_MS=>5000,CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,CURLOPT_SSLVERSION=>CURL_SSLVERSION_TLSv1_2,CURLOPT_PROXY=>'',CURLOPT_RESOLVE=>[$endpoint['host'].':443:'.$target],CURLOPT_HTTPHEADER=>['Accept: application/json','Accept-Encoding: identity'],CURLOPT_USERAGENT=>'NWO-Network-Status/128.0.0',CURLOPT_HEADERFUNCTION=>static function($ch,$line) use (&$headers) { $parts=explode(':',$line,2);if(count($parts)===2){$headers[strtolower(trim($parts[0]))]=trim($parts[1]);}return strlen($line);},CURLOPT_WRITEFUNCTION=>static function($ch,$part) use (&$body) {if(strlen($body)+strlen($part)>524288){return 0;}$body.=$part;return strlen($part);}]);
            $ok=curl_exec($h);$status=curl_getinfo($h,CURLINFO_RESPONSE_CODE);$peer=curl_getinfo($h,CURLINFO_PRIMARY_IP);
            if ($ok===false || $status!==200 || inet_pton($peer)!==inet_pton($ip) || strtolower(explode(';',$headers['content-type']??'')[0])!=='application/json' || !in_array(strtolower($headers['content-encoding']??'identity'),['identity',''],true)) { throw new RuntimeException('gateway_unavailable_or_invalid'); }
            return NWO_Status_Validation::feed($body);
        } finally { curl_close($h); }
    }
}
