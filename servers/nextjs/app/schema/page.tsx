'use client'
import React from 'react'
import { useSearchParams } from 'next/navigation'
import { useLayout } from '../(presentation-generator)/context/LayoutContext'
const page = () => {
    const searchParams = useSearchParams()
    const group = searchParams.get('group')
    const { getLayoutsByGroup, loading } = useLayout()
    if (!group) {
        return <div>No group provided</div>
    }
    const layouts = getLayoutsByGroup(group)
    return (
        <div>
            {loading ? (
                <div>Loading...</div>
            ) : (
                <div data-layouts={JSON.stringify(layouts)}>
                    <pre>{JSON.stringify(layouts, null, 2)}</pre>
                </div>
            )}
        </div>
    )
}

export default page
