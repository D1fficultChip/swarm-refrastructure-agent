import {defineConfig} from '@playwright/test'
export default defineConfig({testDir:'./e2e',timeout:60000,use:{baseURL:'http://127.0.0.1:5173',viewport:{width:1600,height:1100},headless:true,launchOptions:{executablePath:process.env.DEMO_CHROME_PATH}},reporter:'list',workers:1})
