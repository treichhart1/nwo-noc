<?php
// Disposable WordPress bootstrap only. No production credentials.
define('WP_INSTALLING', true);
$_SERVER['HTTP_HOST']='127.0.0.1:18378';$_SERVER['REQUEST_METHOD']='GET';
require '/var/www/html/wp-load.php';
require_once ABSPATH.'wp-admin/includes/upgrade.php';
add_filter('pre_wp_mail','__return_true');
if (!is_blog_installed()) { wp_install('Synthetic NOC128 acceptance', 'noc128-admin', getenv('NOC128_PASSWORD'), 'test@example.invalid', 0); }
update_option('home','http://127.0.0.1:18378');update_option('siteurl','http://127.0.0.1:18378');
update_option('blog_public',0);update_option('default_ping_status','closed');
$id=wp_insert_post(['post_title'=>'Synthetic network status','post_name'=>'synthetic-network-status','post_status'=>'publish','post_type'=>'page','post_content'=>'[nwo_network_status]']);
echo json_encode(['page_id'=>$id,'wordpress'=>$wp_version,'php'=>PHP_VERSION]);
