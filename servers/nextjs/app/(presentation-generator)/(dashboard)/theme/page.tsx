import React from 'react'
import ThemePanel from './components/ThemePanel'
import ThemeComposer from './components/ThemeComposer'
const page = () => {
    return (
        <>
            <ThemePanel />
            <div className="mx-auto w-full max-w-5xl px-4 py-8">
                <ThemeComposer />
            </div>
        </>
    )
}

export default page
