import React from 'react'
import DashboardSidebar from './Components/DashboardSidebar'
import DashboardNav from './Components/DashboardNav'

const layout = ({ children }: { children: React.ReactNode }) => {
    return (
        <div className='flex  gap-6 pr-4 bg-white'>
            <DashboardSidebar />
            <div className='w-full'>
                <DashboardNav />
                {children}
            </div>
        </div>
    )
}

export default layout