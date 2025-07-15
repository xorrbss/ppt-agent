'use client'
import React from 'react'
import { useLayoutLoader } from './hooks/useLayoutLoader'
import LoadingStates from './components/LoadingStates'
import { Card } from '@/components/ui/card'

/**
 * Layout Preview Page
 * 
 * Simple vertical display of all layout components with their sample data.
 */
const LayoutPreview = () => {
    const { layouts, loading, error, retry } = useLayoutLoader()

    // Handle loading state
    if (loading) {
        return <LoadingStates type="loading" />
    }

    // Handle error state
    if (error) {
        return <LoadingStates type="error" message={error} onRetry={retry} />
    }

    // Handle empty state
    if (layouts.length === 0) {
        return <LoadingStates type="empty" onRetry={retry} />
    }

    return (
        <div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-indigo-50">
            {/* Header */}
            <header className="bg-white z-20 backdrop-blur-sm border-b border-white/20 shadow-sm sticky top-0 ">
                <div className="max-w-4xl mx-auto px-6 py-4">
                    <div className="text-center">
                        <h1 className="text-2xl font-bold text-gray-900">Layout Preview</h1>
                        <p className="text-sm text-gray-600 mt-1">
                            {layouts.length} layout{layouts.length !== 1 ? 's' : ''} found
                        </p>
                    </div>
                </div>
            </header>

            {/* Layouts List */}
            <main className="max-w-7xl mx-auto  space-y-8">
                {layouts.map((layout, index) => {
                    const { component: LayoutComponent, sampleData, name, fileName } = layout

                    return (
                        <Card key={index} className="overflow-hidden py-0 shadow-lg border-0 bg-white">
                            {/* Layout Header */}
                            <div className="bg-gray-50 px-6 py-4 border-b border-gray-200">
                                <div className="flex items-center justify-between">
                                    <div>
                                        <h2 className="text-lg font-semibold text-gray-900">{name}</h2>
                                        <p className="text-sm text-gray-600 font-mono">{fileName}</p>
                                    </div>
                                    <div className="px-3 py-1 bg-blue-100 text-blue-700 rounded-md text-sm font-medium">
                                        #{index + 1}
                                    </div>
                                </div>
                            </div>

                            {/* Layout Content */}

                            <LayoutComponent data={sampleData} />

                        </Card>
                    )
                })}
            </main>
            {/* Footer */}
            <footer className="mt-16 bg-white/50 backdrop-blur-sm border-t border-white/20">
                <div className="max-w-4xl mx-auto px-6 py-8">
                    <div className="text-center">
                        <p className="text-sm text-gray-600">
                            Layout Preview • {layouts.length} component{layouts.length !== 1 ? 's' : ''} rendered
                        </p>
                    </div>
                </div>
            </footer>
        </div>
    )
}

export default LayoutPreview
