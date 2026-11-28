<?php
/**
 * Plugin Name: Northwest Ohio Broadband Customer Cancellation
 * Description: Approved HTTPS cancellation request, shortcode and dynamic block. Request data stays on the dedicated signup gateway.
 * Version: 131.0.0
 * Requires at least: 6.5
 * Requires PHP: 8.1
 * License: GPL-2.0-or-later
 */
if(!defined('ABSPATH')){exit;}
require_once __DIR__.'/transport.php';
final class NWO_Customer_Cancellation {
    const OPTION='nwo_customer_cancellation';
    public static function settings(): array { $stored=get_option(self::OPTION,[]);return array_replace(['enabled'=>false,'gateway_url'=>'','gateway_ips'=>[],'brand'=>'Northwest Ohio Broadband','height'=>1800],is_array($stored)?$stored:[]); }
    public static function activate(bool $network_wide=false): void {if($network_wide||version_compare(PHP_VERSION,'8.1','<')||!function_exists('curl_init')){wp_die(esc_html('Per-site activation requires PHP8.1+ and cURL with verified TLS.'));}add_option(self::OPTION,self::settings(),'','no');}
    public static function save(): never {
        if(!current_user_can('manage_options')){wp_die(esc_html('Administrator required.'),403);}check_admin_referer('nwo_cancellation_save');
        try {
            $input=wp_unslash($_POST);$settings=self::settings();$settings['enabled']=!empty($input['enabled']);
            $brand=sanitize_text_field($input['brand']??'');if(strlen($brand)<3||strlen($brand)>100){throw new RuntimeException('invalid_brand');}
            $height=filter_var($input['height']??null,FILTER_VALIDATE_INT);if($height<600||$height>2400){throw new RuntimeException('invalid_height');}
            $settings['brand']=$brand;$settings['height']=$height;
            if($settings['enabled']){
                if(empty($input['approve_origin'])){throw new RuntimeException('approval_required');}
                $origin=rtrim(trim($input['origin']??''),'/');$endpoint=NWO_Cancellation_Transport::endpoint($origin.'/public/cancellation/health');
                $settings['gateway_url']=$endpoint['url'];$settings['gateway_ips']=NWO_Cancellation_Transport::resolve($endpoint['host']);
            }
            update_option(self::OPTION,$settings,false);delete_option('nwo_cancellation_diagnostics');
        }catch(Throwable $error){wp_die(esc_html('Settings rejected. Approve an exact public HTTPS origin on port443 with public DNS and bounded branding/height. Existing settings preserved.'));}
        wp_safe_redirect(admin_url('options-general.php?page=nwo-customer-cancellation'));exit;
    }
    public static function check(): never {
        if(!current_user_can('manage_options')){wp_die(esc_html('Administrator required.'),403);}check_admin_referer('nwo_cancellation_check');
        $s=self::settings();$state='unavailable';try{NWO_Cancellation_Transport::fetch($s);$state='connected';}catch(Throwable $error){}
        update_option('nwo_cancellation_diagnostics',['state'=>$state,'checked_at'=>gmdate('c')],false);
        wp_safe_redirect(admin_url('options-general.php?page=nwo-customer-cancellation'));exit;
    }
    public static function render(): string {
        $s=self::settings();if(!$s['enabled']){return '<p class="nwo-cancellation-unavailable">Online cancellation requests are not configured. Please contact our office.</p>';}
        try{$endpoint=NWO_Cancellation_Transport::endpoint($s['gateway_url']);$origin='https://'.$endpoint['host'];}catch(Throwable $error){return '<p>Online cancellation requests are unavailable. Please contact our office.</p>';}
        wp_enqueue_style('nwo-customer-cancellation',plugins_url('assets/cancellation.css',__FILE__),[],'131.0.0');
        return '<section class="nwo-cancellation-embed" aria-label="'.esc_attr($s['brand'].' cancellation request').'"><h2>'.esc_html($s['brand']).' — Request cancellation</h2><p>Cancellation requests require identity verification and operator review. Submission does not disconnect service or change billing.</p><iframe class="nwo-cancellation-frame" title="Secure cancellation request" src="'.esc_url($origin.'/cancel.html').'" height="'.esc_attr((string)$s['height']).'" loading="lazy" referrerpolicy="no-referrer" sandbox="allow-forms allow-scripts allow-same-origin"></iframe><p><a href="'.esc_url($origin.'/cancel.html').'" target="_blank" rel="noopener noreferrer">Open secure cancellation page</a> if your browser blocks embedded forms. Customer information is submitted to the dedicated signup service, not stored in WordPress.</p></section>';
    }
    public static function admin(): void {
        if(!current_user_can('manage_options')){return;}$s=self::settings();$origin=$s['gateway_url']?preg_replace('#/public/cancellation/health$#','',$s['gateway_url']):'';
        echo '<div class="wrap"><h1>NWO customer cancellation</h1><p>Keep disabled until separately authorized. Intended origin: https://signup.nwohiobb.com. No private NOC API or credential belongs here.</p><form method="post" action="'.esc_url(admin_url('admin-post.php')).'">';wp_nonce_field('nwo_cancellation_save');echo '<input type="hidden" name="action" value="nwo_cancellation_save"/><label><input type="checkbox" name="enabled" value="1" '.checked($s['enabled'],true,false).'/>Enable approved cancellation integration</label><p><label>Approved HTTPS signup origin <input name="origin" type="url" value="'.esc_attr($origin).'"/></label></p><p><label>Brand <input name="brand" value="'.esc_attr($s['brand']).'" maxlength="100"/></label></p><p><label>Frame height <input name="height" type="number" min="600" max="2400" value="'.esc_attr((string)$s['height']).'"/></label></p><label><input name="approve_origin" type="checkbox" value="1"/>I approve this public origin, its public DNS addresses, HTTPS certificate and privacy boundary</label>';submit_button('Save cancellation settings');echo '</form><form method="post" action="'.esc_url(admin_url('admin-post.php')).'">';wp_nonce_field('nwo_cancellation_check');echo '<input type="hidden" name="action" value="nwo_cancellation_check"/>';submit_button('Check connection');echo '</form><p>Diagnostics: '.esc_html(wp_json_encode(get_option('nwo_cancellation_diagnostics',['state'=>'not_verified']))).'</p><p>Shortcode: <code>[nwo_customer_cancellation]</code>. Gutenberg: NWO Customer Cancellation.</p></div>';
    }
}
register_activation_hook(__FILE__,[NWO_Customer_Cancellation::class,'activate']);
add_action('admin_menu',static function(){add_options_page('NWO customer cancellation','NWO customer cancellation','manage_options','nwo-customer-cancellation',[NWO_Customer_Cancellation::class,'admin']);});
add_action('admin_post_nwo_cancellation_save',[NWO_Customer_Cancellation::class,'save']);
add_action('admin_post_nwo_cancellation_check',[NWO_Customer_Cancellation::class,'check']);
add_shortcode('nwo_customer_cancellation',[NWO_Customer_Cancellation::class,'render']);
add_action('init',static function(){wp_register_script('nwo-cancellation-editor',plugins_url('assets/editor.js',__FILE__),['wp-blocks','wp-element'],'131.0.0',true);register_block_type(__DIR__,['render_callback'=>[NWO_Customer_Cancellation::class,'render']]);});
