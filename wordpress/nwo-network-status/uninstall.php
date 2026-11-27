<?php
// SPDX-License-Identifier: GPL-2.0-or-later
if (!defined('WP_UNINSTALL_PLUGIN')) { exit; }
foreach (['nwo_status_settings','nwo_status_diagnostics'] as $key) { delete_option($key); }
for ($i=0;$i<=180;$i+=20) { foreach (['cache','failure','lock'] as $kind) { delete_option('nwo_status_'.$kind.'_'.$i); } }
// Never remove pages, incident history, customer records or NOC data.
