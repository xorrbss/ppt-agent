'use client'
import React, { useRef } from 'react'
import { useLayoutLoader } from './hooks/useLayoutLoader'
import LoadingStates from './components/LoadingStates'
import { Card } from '@/components/ui/card'


const LayoutPreview = () => {
    const { layoutGroups, layouts, loading, error, retry } = useLayoutLoader()
    const sectionRefs = useRef<Record<string, HTMLElement | null>>({})

    const scrollToSection = (groupName: string) => {
        const element = sectionRefs.current[groupName]
        if (element) {
            const headerHeight = 140 // Account for sticky header + nav
            const elementPosition = element.offsetTop - headerHeight
            window.scrollTo({
                top: elementPosition,
                behavior: 'smooth'
            })
        }
    }

    const setSectionRef = (groupName: string) => (el: HTMLElement | null) => {
        sectionRefs.current[groupName] = el
    }

    // Handle loading state
    if (loading) {
        return <LoadingStates type="loading" />
    }

    // Handle error state
    if (error) {
        return <LoadingStates type="error" message={error} onRetry={retry} />
    }

    // Handle empty state
    if (layoutGroups.length === 0 || layouts.length === 0) {
        return <LoadingStates type="empty" onRetry={retry} />
    }

    return (
        <div className="min-h-screen bg-gray-50">
            {/* Header */}
            <header className="bg-white shadow-sm border-b sticky top-0 z-30">
                <div className="max-w-7xl mx-auto px-6 py-6">
                    <div className="text-center">
                        <h1 className="text-3xl font-bold text-gray-900">Layout Preview</h1>
                        <p className="text-gray-600 mt-2">
                            {layoutGroups.length} groups • {layouts.length} layouts
                        </p>
                    </div>
                </div>

                {/* Group Navigation Tags */}
                <div className="border-t bg-gray-50">
                    <div className="max-w-7xl mx-auto px-6 py-4">
                        <div className="flex flex-wrap gap-3 justify-center">
                            {layoutGroups.map((group) => (
                                <button
                                    key={group.group}
                                    onClick={() => scrollToSection(group.group)}
                                    className="inline-flex items-center px-4 py-2 rounded-full text-sm font-medium bg-white border border-gray-200 text-gray-700 hover:bg-blue-50 hover:border-blue-300 hover:text-blue-700 transition-all duration-200 shadow-sm hover:shadow-md"
                                >
                                    <span className="capitalize">{group.group}</span>
                                    <span className="ml-2 px-2 py-0.5 bg-gray-100 text-gray-600 rounded-full text-xs">
                                        {group.layouts.length}
                                    </span>
                                </button>
                            ))}
                        </div>
                    </div>
                </div>
            </header>

            {/* Layout Groups */}
            <main className="max-w-7xl mx-auto px-6 py-8">
                <div className="space-y-12">
                    {layoutGroups.map((group) => (
                        <section
                            key={group.group}
                            ref={setSectionRef(group.group)}
                            className="space-y-6"
                        >
                            {/* Group Title */}
                            <div className="border-b border-gray-200 pb-4">
                                <h2 className="text-2xl font-bold text-gray-900 capitalize">
                                    {group.group} Layouts
                                </h2>
                                <p className="text-gray-600 mt-1">
                                    {group.layouts.length} layout{group.layouts.length !== 1 ? 's' : ''}
                                </p>
                            </div>

                            {/* Group Layouts Grid */}
                            <div className="space-y-8">
                                {group.layouts.map((layout, index) => {
                                    const { component: LayoutComponent, sampleData, name, fileName } = layout

                                    return (
                                        <Card key={`${group.group}-${index}`} className="overflow-hidden shadow-md hover:shadow-lg transition-shadow">
                                            {/* Layout Header */}
                                            <div className="bg-white px-6 py-4 border-b">
                                                <div className="flex items-center justify-between">
                                                    <div>
                                                        <h3 className="text-xl font-semibold text-gray-900">{name}</h3>
                                                        <div className="flex items-center gap-4 mt-1">
                                                            <span className="text-sm text-gray-500 font-mono">{fileName}</span>
                                                            <span className="inline-flex items-center px-2 py-1 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                                                                {group.group}
                                                            </span>
                                                        </div>
                                                    </div>
                                                    <div className="text-right">
                                                        <div className="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium bg-gray-100 text-gray-700">
                                                            Layout #{index + 1}
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>

                                            {/* Layout Content */}
                                            <div className="bg-gray-50">
                                                <LayoutComponent data={sampleData} />
                                            </div>
                                        </Card>
                                    )
                                })}
                            </div>
                        </section>
                    ))}
                </div>
            </main>

            {/* Footer */}
            <footer className="bg-white border-t mt-16">
                <div className="max-w-7xl mx-auto px-6 py-8">
                    <div className="text-center text-gray-600">
                        <p>Layout Preview System • {layoutGroups.length} groups • {layouts.length} components</p>
                    </div>
                </div>
            </footer>
        </div>
    )
}

export default LayoutPreview
