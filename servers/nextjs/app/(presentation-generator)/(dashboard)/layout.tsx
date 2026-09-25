import React from 'react'

const layout = ({ children }: { children: React.ReactNode }) => {
    return (
        <div className='min-h-screen min-w-0 bg-[#fafafa]'>
            {children}
        </div>
    )
}

export default layout
