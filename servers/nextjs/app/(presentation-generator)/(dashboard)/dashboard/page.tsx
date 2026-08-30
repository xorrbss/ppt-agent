import React from 'react'
import DashboardPage from './components/DashboardPage'

const enabled = (value: string | undefined) =>
  ["1", "true", "yes", "on"].includes(value?.trim().toLowerCase() ?? "");

const page = () => {
  return (
    <DashboardPage
      publicV2CanaryEnabled={enabled(process.env.PUBLIC_V2_WEB_CANARY_ENABLED)}
    />
  )
}

export default page
