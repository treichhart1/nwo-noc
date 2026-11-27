<?php
if(!defined('WP_UNINSTALL_PLUGIN')){exit;}
delete_option('nwo_customer_cancellation');delete_option('nwo_cancellation_diagnostics');
// Preserve WordPress pages and all NOC application/consent/history data.
