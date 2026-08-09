import React from 'react'
import SettingPage from './SettingPage'
import UserAccountSettings from './UserAccountSettings'
import { getServerAuthStatus } from '@/utils/serverAuth'
import { getSettingsView } from '@/utils/settingsAccess'
import { getServerI18n } from '@/i18n/server'

export const metadata = {
  title: '설정 | Presenton',
  description: '설정 페이지',
}
const page = async () => {
  const status = await getServerAuthStatus()
  const { t } = await getServerI18n()

  return getSettingsView(status.role) === 'admin'
    ? <SettingPage />
    : <UserAccountSettings username={status.username ?? t('common.unknown')} />
}

export default page
