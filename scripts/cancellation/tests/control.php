<?php
$_SERVER['HTTP_HOST']='wordpress.noc128.test';$_SERVER['HTTPS']='on';$_SERVER['REQUEST_METHOD']='GET';
require '/var/www/html/wp-load.php';require_once ABSPATH.'wp-admin/includes/file.php';require_once ABSPATH.'wp-admin/includes/plugin.php';
$action=getenv('NOC131_ACTION');
if($action==='page'){
$id=wp_insert_post(['post_title'=>'Synthetic cancellation','post_name'=>'synthetic-cancellation','post_type'=>'page','post_status'=>'publish','post_content'=>'[nwo_customer_cancellation]']);
$block=wp_insert_post(['post_title'=>'Cancellation block','post_name'=>'cancellation-block','post_type'=>'page','post_status'=>'publish','post_content'=>'<!-- wp:nwo/customer-cancellation /-->']);
echo json_encode(['page'=>$id,'block'=>$block]);
}elseif($action==='contracts'){
$count=0;foreach(['http://signup.example.invalid','https://127.0.0.1','https://10.0.0.1','https://user:secret@signup.example.invalid','https://signup.example.invalid:8443','https://signup.example.invalid/path','https://signup.example.invalid?token=bad'] as $origin){$denied=false;try{NWO_Cancellation_Transport::endpoint($origin.'/public/cancellation/health');}catch(Throwable $e){$denied=true;}if(!$denied){throw new RuntimeException('Unsafe origin accepted');}$count++;}
if(!is_plugin_active('nwo-customer-cancellation/nwo-customer-cancellation.php')){throw new RuntimeException('Plugin inactive');}
echo json_encode(['origin_rejections'=>$count]);
}elseif($action==='classic'){switch_theme('noc128classic');echo '{}';
}elseif($action==='deactivate'){deactivate_plugins('nwo-customer-cancellation/nwo-customer-cancellation.php');echo '{}';
}elseif($action==='uninstall'){delete_plugins(['nwo-customer-cancellation/nwo-customer-cancellation.php']);if(is_file(WP_PLUGIN_DIR.'/nwo-customer-cancellation/nwo-customer-cancellation.php')||get_option('nwo_customer_cancellation',null)!==null){throw new RuntimeException('Plugin data remains');}if(!get_page_by_path('synthetic-cancellation')){throw new RuntimeException('Page removed');}echo json_encode(['removed'=>true,'page_preserved'=>true]);
}else{throw new RuntimeException('Unknown synthetic action');}
