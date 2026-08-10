import React from 'react'
import DashboardSidebar from './Components/DashboardSidebar'

const layout = ({ children }: { children: React.ReactNode }) => {
    return (
        <div className='flex min-h-screen min-w-0 bg-white pb-20 sm:pb-0 sm:pr-4'>
            <DashboardSidebar />
            <div className='min-w-0 w-full'>

                {children}
            </div>
        </div>
    )
}

export default layout
