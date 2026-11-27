<?php
if(!defined('WP_UNINSTALL_PLUGIN')){exit;}
delete_option('nwo_customer_signup');delete_option('nwo_signup_diagnostics');
// Preserve WordPress pages and all NOC application/consent/history data.
