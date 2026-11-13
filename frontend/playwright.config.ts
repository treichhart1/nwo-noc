import {defineConfig} from '@playwright/test'
export default defineConfig({
  testDir: './tests', timeout: 90000, expect: {timeout: 30000}, workers: 1, retries: 0, reporter: 'list',
  use: {baseURL: 'http://127.0.0.1:18090', headless: true,
    launchOptions: process.env.NOC_CHROMIUM_PATH ? {executablePath: process.env.NOC_CHROMIUM_PATH} : {}},
})
