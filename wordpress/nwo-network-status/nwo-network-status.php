<?php
/**
 * Plugin Name: NWO Network Status
 * Description: Approved public network updates, accessible banners and read-only NOC gateway integration.
 * Version: 128.0.0
 * Requires at least: 6.5
 * Requires PHP: 8.1
 * License: GPL-2.0-or-later
 * Text Domain: nwo-network-status
 */
if (!defined('ABSPATH')) { exit; }
require_once __DIR__.'/validation.php';require_once __DIR__.'/transport.php';
final class NWO_Network_Status {
    public const OPTION='nwo_status_settings';
    public static function defaults(): array { return ['enabled'=>false,'gateway_url'=>'','gateway_ips'=>[],'refresh'=>60,'brand'=>'Northwest Ohio Broadband','support_text'=>'Contact support through the usual channels.','support_url'=>'','theme'=>'auto','placement'=>'none','colors'=>['operational'=>'#16734a','disruption'=>'#b42318','unknown'=>'#805900'],'labels'=>NWO_Status_Validation::LABELS]; }
    public static function settings(): array { $s=get_option(self::OPTION,[]);return array_replace(self::defaults(),is_array($s)?$s:[]); }
    public static function activate(bool $network_wide=false): void { if ($network_wide || version_compare(PHP_VERSION,'8.1','<') || (!function_exists('curl_init') || !function_exists('mb_strlen'))) { wp_die(esc_html('Per-site activation requires PHP 8.1 or later and cURL with TLS and mbstring.')); }add_option(self::OPTION,self::defaults(),'','no'); }
    public static function clear_cache(): void { for($i=0;$i<=180;$i+=20){delete_option('nwo_status_cache_'.$i);delete_option('nwo_status_failure_'.$i);delete_option('nwo_status_lock_'.$i);} }
    public static function deactivate(): void { self::clear_cache(); }
    private static function administrator(string $nonce): void { if (!current_user_can('manage_options')) { wp_die(esc_html('Administrator capability required.'),403); }check_admin_referer($nonce); }
    public static function save(): never {
        self::administrator('nwo_status_save');$v=wp_unslash($_POST);$s=self::defaults();
        try {
            $s['brand']=sanitize_text_field($v['brand']??'');$s['support_text']=sanitize_text_field($v['support_text']??'');
            if (mb_strlen($s['brand'])<3 || mb_strlen($s['brand'])>100 || mb_strlen($s['support_text'])>200) { throw new RuntimeException('invalid_branding'); }
            $s['support_url']=trim($v['support_url']??'');if ($s['support_url']!=='' && (!wp_http_validate_url($s['support_url']) || strtolower(wp_parse_url($s['support_url'],PHP_URL_SCHEME)??'')!=='https')) { throw new RuntimeException('invalid_support_url'); }
            $s['refresh']=filter_var($v['refresh']??null,FILTER_VALIDATE_INT);if ($s['refresh']<15 || $s['refresh']>60) { throw new RuntimeException('invalid_refresh'); }
            foreach (['theme'=>['auto','light','dark'],'placement'=>['none','body_open','footer']] as $k=>$allowed) { if (!in_array($v[$k]??null,$allowed,true)) { throw new RuntimeException('invalid_appearance'); }$s[$k]=$v[$k]; }
            $colors=json_decode($v['colors']??'',true,8,JSON_THROW_ON_ERROR);if(!is_array($colors)||array_keys($colors)!==array_keys($s['colors'])){throw new RuntimeException('invalid_colors');}foreach($colors as $color){if(!is_string($color)||!preg_match('/^#[a-fA-F0-9]{6}$/D',$color)){throw new RuntimeException('invalid_colors');}}$s['colors']=$colors;
            $labels=json_decode($v['labels']??'',true,8,JSON_THROW_ON_ERROR);if(!is_array($labels)||array_diff(array_keys($labels),array_keys($s['labels']))){throw new RuntimeException('invalid_labels');}foreach($labels as $key=>$label){if(!is_string($label)||mb_strlen($label)<3||mb_strlen($label)>80||$label!==sanitize_text_field($label)){throw new RuntimeException('invalid_labels');}if($key!=='unknown'){$s['labels'][$key]=$label;}}
            if (!empty($v['enabled'])) { if(empty($v['approve_gateway'])){throw new RuntimeException('gateway_approval_required');}$e=NWO_Status_Transport::endpoint(trim($v['gateway_url']??''));$s['gateway_url']=$e['url'];$s['gateway_ips']=NWO_Status_Transport::resolve($e['host']);$s['enabled']=true; }
            update_option(self::OPTION,$s,false); self::clear_cache(); update_option('nwo_status_diagnostics',['state'=>'not_verified','last_success'=>null],false);
        } catch (Throwable $error) { wp_die(esc_html('Settings rejected. Check the public HTTPS URL, public DNS addresses, approval and bounded appearance values. No host changes were made.')); }
        wp_safe_redirect(admin_url('options-general.php?page=nwo-network-status'));exit;
    }
    public static function refresh_admin(): never { self::administrator('nwo_status_refresh');self::clear_cache();self::data(0);wp_safe_redirect(admin_url('options-general.php?page=nwo-network-status'));exit; }
    private static function unknown(): array { $d=get_option('nwo_status_diagnostics',[]);return ['available'=>false,'overall_status'=>'unknown','freshness'=>'unavailable','last_success'=>$d['last_success']??null,'incidents'=>[],'maintenance'=>[],'resolved'=>[],'service_areas'=>[]]; }
    public static function data(int $offset): array {
        $s=self::settings();if(!$s['enabled']){return self::unknown();}$key='nwo_status_cache_'.$offset;$cached=get_option($key,null);
        try { if (is_array($cached) && ($cached['saved']??0)>time()-15) { $cached['feed']=NWO_Status_Validation::feed(wp_json_encode($cached['feed'])); return self::envelope($cached); } } catch (Throwable $error) { delete_option($key); }
        if (get_option('nwo_status_failure_'.$offset,0)>time()) { return self::unknown(); }
        $lock='nwo_status_lock_'.$offset;$until=get_option($lock,0);if($until && $until<time()){delete_option($lock);}if(!add_option($lock,time()+15,'','no')){return self::unknown();}
        try {
            $feed=NWO_Status_Transport::fetch($s,$offset);
            if ($offset===0 && is_array($cached) && ($cached['feed']['revision_token']??'')!==$feed['revision_token']) { for($i=20;$i<=180;$i+=20){delete_option('nwo_status_cache_'.$i);} }
            if ($offset>0) { $root=self::data(0);if(empty($root['available'])||$root['feed']['revision_token']!==$feed['revision_token']){throw new RuntimeException('publication_changed');} }
            $value=['saved'=>time(),'feed'=>$feed];update_option($key,$value,false);update_option('nwo_status_diagnostics',['state'=>$feed['freshness']==='fresh'?'connected':'stale','last_success'=>gmdate('c')],false);delete_option('nwo_status_failure_'.$offset);return self::envelope($value);
        } catch (Throwable $error) { $d=get_option('nwo_status_diagnostics',[]);update_option('nwo_status_diagnostics',['state'=>'unavailable','last_success'=>$d['last_success']??null],false);update_option('nwo_status_failure_'.$offset,time()+15,false);return self::unknown(); }
        finally { delete_option($lock); }
    }
    private static function envelope(array $cache): array { return ['available'=>true,'feed'=>$cache['feed'],'last_success'=>gmdate('c',$cache['saved']),'valid_until'=>$cache['feed']['expires_at']]; }
    public static function rest(WP_REST_Request $request): WP_REST_Response {
        $q=$request->get_query_params();if(array_diff(array_keys($q),['history_offset','rest_route'])){return new WP_REST_Response(['available'=>false,'overall_status'=>'unknown'],400,['Cache-Control'=>'no-store']);}
        $offset=$q['history_offset']??0;if(!is_scalar($offset)||!ctype_digit((string)$offset)||(int)$offset>180||(int)$offset%20!==0){return new WP_REST_Response(['available'=>false,'overall_status'=>'unknown'],400,['Cache-Control'=>'no-store']);}
        $data=self::data((int)$offset);return new WP_REST_Response($data,$data['available']?200:503,['Cache-Control'=>'no-store, max-age=0','X-Content-Type-Options'=>'nosniff']);
    }
    public static function assets(): void { wp_enqueue_style('nwo-network-status',plugins_url('assets/status.css',__FILE__),[],'128.0.0');wp_enqueue_script('nwo-network-status',plugins_url('assets/status.js',__FILE__),[],'128.0.0',true); }
    public static function render($attributes=[]): string {
        $attributes=is_array($attributes)?$attributes:[];
        self::assets();$s=self::settings();$view=($attributes['view']??'full')==='banner'?'banner':'full';
        $public=['brand'=>$s['brand'],'support_text'=>$s['support_text'],'support_url'=>$s['support_url'],'theme'=>$s['theme'],'colors'=>$s['colors'],'labels'=>$s['labels'],'refresh'=>$s['refresh'],'view'=>$view];
        return '<section class="nwo-network-status" data-nwo-status-endpoint="'.esc_url(rest_url('nwo-status/v1/feed')).'" data-nwo-status-options="'.esc_attr(wp_json_encode($public)).'" aria-label="'.esc_attr($s['brand'].' network status').'"><h2>'.esc_html($s['brand']).'</h2><p role="status">'.esc_html('Status unknown — loading approved public updates.').'</p></section>';
    }
    public static function register(): void { register_rest_route('nwo-status/v1','/feed',['methods'=>'GET','callback'=>[self::class,'rest'],'permission_callback'=>'__return_true']); }
    public static function blocks(): void {
        wp_register_script('nwo-status-editor',plugins_url('assets/editor.js',__FILE__),['wp-blocks','wp-element','wp-components','wp-block-editor'],'128.0.0',true);
        register_block_type(__DIR__.'/block.json',['editor_script'=>'nwo-status-editor','render_callback'=>[self::class,'render']]);
    }
    public static function banner(string $position): void { if(self::settings()['placement']===$position){echo self::render(['view'=>'banner']);} }
    public static function admin(): void {
        if(!current_user_can('manage_options')){return;}$s=self::settings();$diag=get_option('nwo_status_diagnostics',['state'=>'not_verified','last_success'=>null]);
        echo '<div class="wrap"><h1>NWO Network Status</h1><p>Connection: '.esc_html($diag['state']).' · Last success: '.esc_html($diag['last_success']??'never').'</p><p>Read-only approved public data only. No credentials, private NOC endpoints or insecure TLS fallback are supported. Save explicitly approves current public DNS addresses; reapprove after a DNS change.</p><form method="post" action="'.esc_url(admin_url('admin-post.php')).'"><input type="hidden" name="action" value="nwo_status_save">';wp_nonce_field('nwo_status_save');
        foreach(['gateway_url'=>'Public gateway HTTPS URL','brand'=>'Public brand','support_text'=>'Public support contact text','support_url'=>'Public HTTPS support page'] as $key=>$label){echo '<p><label>'.esc_html($label).' <input class="regular-text" name="'.esc_attr($key).'" value="'.esc_attr($s[$key]).'" type="'.($key==='gateway_url'||$key==='support_url'?'url':'text').'"></label></p>';}
        echo '<p><label><input type="checkbox" name="enabled" value="1" '.checked($s['enabled'],true,false).'> Enable public retrieval</label></p><p><label><input type="checkbox" name="approve_gateway" value="1"> I approve this public gateway URL and its validated public IP addresses</label></p><p><label>Refresh interval (15–60 seconds) <input name="refresh" type="number" min="15" max="60" value="'.esc_attr((string)$s['refresh']).'"></label></p>';
        foreach(['theme'=>['auto','light','dark'],'placement'=>['none','body_open','footer']] as $key=>$values){echo '<p><label>'.esc_html(ucfirst($key)).' <select name="'.esc_attr($key).'">';foreach($values as $value){echo '<option value="'.esc_attr($value).'" '.selected($s[$key],$value,false).'>'.esc_html($value).'</option>';}echo '</select></label></p>';}
        foreach(['colors','labels'] as $key){echo '<p><label>'.esc_html(ucfirst($key)).' JSON <textarea name="'.esc_attr($key).'" rows="5" cols="80">'.esc_textarea(wp_json_encode($s[$key])).'</textarea></label></p>';}
        submit_button('Save reviewed settings');echo '</form><form method="post" action="'.esc_url(admin_url('admin-post.php')).'"><input type="hidden" name="action" value="nwo_status_refresh">';wp_nonce_field('nwo_status_refresh');submit_button('Refresh cache and connection diagnostics','secondary');echo '</form><p>Full page: <code>[nwo_network_status]</code>; compact banner: <code>[nwo_network_status view="banner"]</code>. Or add the NWO Network Status block in Gutenberg.</p></div>';
    }
}
register_activation_hook(__FILE__,[NWO_Network_Status::class,'activate']);register_deactivation_hook(__FILE__,[NWO_Network_Status::class,'deactivate']);
add_shortcode('nwo_network_status',[NWO_Network_Status::class,'render']);add_action('init',[NWO_Network_Status::class,'blocks']);add_action('rest_api_init',[NWO_Network_Status::class,'register']);
add_action('admin_post_nwo_status_save',[NWO_Network_Status::class,'save']);add_action('admin_post_nwo_status_refresh',[NWO_Network_Status::class,'refresh_admin']);
add_action('admin_menu',static function(){add_options_page('NWO Network Status','NWO Network Status','manage_options','nwo-network-status',[NWO_Network_Status::class,'admin']);});
add_action('wp_body_open',static function(){NWO_Network_Status::banner('body_open');});add_action('wp_footer',static function(){NWO_Network_Status::banner('footer');});

// Load namespaced styles in the head; render callbacks can run after wp_head.
add_action('wp_enqueue_scripts',[NWO_Network_Status::class,'assets']);
