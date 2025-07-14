'use client'
import { toast } from '@/hooks/use-toast'
import React, { useState, useEffect } from 'react'

interface LayoutInfo {
    name: string
    component: React.ComponentType<any>
    schema: any
    sampleData: any
    fileName: string
}

const LayoutPreview = () => {
    const [layouts, setLayouts] = useState<LayoutInfo[]>([])
    const [loading, setLoading] = useState(true)
    const [currentLayout, setCurrentLayout] = useState(0)
    const [error, setError] = useState<string | null>(null)

    const generateSampleDataFromSchema = (schema: any, layoutName: string): any => {
        if (!schema) return {}

        try {
            // First, try to get defaults from schema
            let sampleData = {}
            try {
                sampleData = schema.parse({})
            } catch {
                // If parsing fails, we'll build the data manually
            }

            // Generate realistic sample data based on schema shape
            const enhancedData = generateRealisticData(schema._def?.shape || schema.shape, layoutName)

            // Merge defaults with enhanced data, giving priority to defaults
            return { ...enhancedData, ...sampleData }
        } catch (error) {
            console.error(`Error generating sample data for ${layoutName}:`, error)
            return {}
        }
    }

    const generateRealisticData = (shape: any, layoutName: string): any => {
        if (!shape) return {}

        const data: any = {}

        for (const [key, fieldSchema] of Object.entries(shape as any)) {
            const field = fieldSchema as any

            // Skip if field has a default value (will be handled by schema.parse)
            if (field._def?.defaultValue !== undefined) {
                continue
            }

            data[key] = generateFieldValue(key, field, layoutName)
        }

        return data
    }

    const generateFieldValue = (fieldName: string, fieldSchema: any, layoutName: string): any => {
        const fieldType = fieldSchema._def?.typeName

        // Handle optional fields (might not generate value for some)
        if (fieldSchema._def?.innerType && Math.random() > 0.7) {
            return undefined
        }

        // Handle different field types
        switch (fieldType) {
            case 'ZodString':
                return generateStringValue(fieldName, fieldSchema, layoutName)
            case 'ZodArray':
                return generateArrayValue(fieldName, fieldSchema, layoutName)
            case 'ZodObject':
                return generateObjectValue(fieldName, fieldSchema, layoutName)
            case 'ZodEnum':
                const options = fieldSchema._def?.values || []
                return options[Math.floor(Math.random() * options.length)]
            case 'ZodBoolean':
                return Math.random() > 0.5
            case 'ZodNumber':
                return Math.floor(Math.random() * 100) + 1
            default:
                return generateStringValue(fieldName, fieldSchema, layoutName)
        }
    }

    const generateStringValue = (fieldName: string, fieldSchema: any, layoutName: string): string => {
        const lowerField = fieldName.toLowerCase()

        // Handle URLs (images, logos, backgrounds, etc.)
        if (lowerField.includes('url') || lowerField.includes('image') || lowerField.includes('logo')) {
            if (lowerField.includes('logo')) {
                return 'https://images.unsplash.com/photo-1611224923853-80b023f02d71?w=200&h=200&fit=crop'
            }
            if (lowerField.includes('background')) {
                const backgrounds = [
                    'https://images.unsplash.com/photo-1557804506-669a67965ba0?w=1920&h=1080&fit=crop',
                    'https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=1920&h=1080&fit=crop',
                    'https://images.unsplash.com/photo-1519389950473-47ba0277781c?w=1920&h=1080&fit=crop'
                ]
                return backgrounds[Math.floor(Math.random() * backgrounds.length)]
            }
            // Regular images
            const images = [
                'https://images.unsplash.com/photo-1551434678-e076c223a692?w=800&h=600&fit=crop',
                'https://images.unsplash.com/photo-1460925895917-afdab827c52f?w=800&h=600&fit=crop',
                'https://images.unsplash.com/photo-1504384308090-c894fdcc538d?w=800&h=600&fit=crop',
                'https://images.unsplash.com/photo-1519389950473-47ba0277781c?w=800&h=600&fit=crop'
            ]
            return images[Math.floor(Math.random() * images.length)]
        }

        // Handle email
        if (lowerField.includes('email')) {
            const domains = ['example.com', 'company.com', 'business.org']
            const names = ['contact', 'info', 'hello', 'support']
            return `${names[Math.floor(Math.random() * names.length)]}@${domains[Math.floor(Math.random() * domains.length)]}`
        }

        // Handle phone
        if (lowerField.includes('phone')) {
            return `+1 (555) ${Math.floor(Math.random() * 900) + 100}-${Math.floor(Math.random() * 9000) + 1000}`
        }

        // Handle website
        if (lowerField.includes('website')) {
            const sites = ['https://example.com', 'https://company.com', 'https://business.org']
            return sites[Math.floor(Math.random() * sites.length)]
        }

        // Handle LinkedIn
        if (lowerField.includes('linkedin')) {
            return 'https://linkedin.com/company/example'
        }

        // Handle specific field names
        if (lowerField.includes('title')) {
            const titles = [
                'Welcome to Our Presentation',
                'Key Business Insights',
                'Product Overview',
                'Market Analysis',
                'Future Vision',
                'Strategic Goals'
            ]
            return titles[Math.floor(Math.random() * titles.length)]
        }

        if (lowerField.includes('subtitle')) {
            const subtitles = [
                'Driving innovation through technology',
                'Transforming the way we work',
                'Building solutions for tomorrow',
                'Excellence in every detail',
                'Your success is our mission'
            ]
            return subtitles[Math.floor(Math.random() * subtitles.length)]
        }

        if (lowerField.includes('author') || lowerField.includes('name')) {
            const names = ['Alex Johnson', 'Sarah Chen', 'Michael Rodriguez', 'Emily Davis', 'David Kim']
            return names[Math.floor(Math.random() * names.length)]
        }

        if (lowerField.includes('organization') || lowerField.includes('company')) {
            const orgs = ['Tech Innovations Inc.', 'Future Solutions Ltd.', 'Global Dynamics Corp.', 'NextGen Enterprises']
            return orgs[Math.floor(Math.random() * orgs.length)]
        }

        if (lowerField.includes('date')) {
            return new Date().toLocaleDateString()
        }

        if (lowerField.includes('content')) {
            const contents = [
                'Our innovative approach combines cutting-edge technology with proven methodologies to deliver exceptional results. We focus on scalability, reliability, and user experience.',
                'Through strategic partnerships and continuous innovation, we\'ve established ourselves as leaders in the industry. Our solutions are designed to meet evolving market demands.',
                'With over a decade of experience, our team brings deep expertise and fresh perspectives to every project. We\'re committed to exceeding expectations and driving growth.'
            ]
            return contents[Math.floor(Math.random() * contents.length)]
        }

        if (lowerField.includes('caption')) {
            const captions = [
                'Innovative solutions driving business transformation',
                'Real-time analytics and insights at your fingertips',
                'Seamless integration with existing workflows',
                'Empowering teams to achieve more'
            ]
            return captions[Math.floor(Math.random() * captions.length)]
        }

        if (lowerField.includes('action') || lowerField.includes('cta')) {
            const actions = [
                'Get Started Today!',
                'Schedule a Demo',
                'Contact Our Team',
                'Learn More',
                'Try It Free'
            ]
            return actions[Math.floor(Math.random() * actions.length)]
        }

        // Default text based on field length constraints
        const minLength = fieldSchema._def?.checks?.find((c: any) => c.kind === 'min')?.value || 10
        const maxLength = fieldSchema._def?.checks?.find((c: any) => c.kind === 'max')?.value || 100

        if (maxLength <= 50) {
            return 'Sample short text content'
        } else if (maxLength <= 150) {
            return 'This is sample medium-length text content for preview purposes'
        } else {
            return 'This is sample long-form text content that demonstrates how the layout will look with realistic data. It provides a good representation of the final presentation slide.'
        }
    }

    const generateArrayValue = (fieldName: string, fieldSchema: any, layoutName: string): any[] => {
        const itemSchema = fieldSchema._def?.type
        const minItems = fieldSchema._def?.minLength?.value || 2
        const maxItems = Math.min(fieldSchema._def?.maxLength?.value || 5, 6)
        const itemCount = Math.floor(Math.random() * (maxItems - minItems + 1)) + minItems

        const lowerField = fieldName.toLowerCase()

        if (lowerField.includes('bullet') || lowerField.includes('point')) {
            const bulletPoints = [
                'Increased efficiency and productivity',
                'Cost-effective solutions',
                'Enhanced user experience',
                'Scalable architecture',
                'Real-time analytics',
                '24/7 customer support',
                'Seamless integration capabilities',
                'Advanced security features'
            ]
            return bulletPoints.slice(0, itemCount)
        }

        if (lowerField.includes('takeaway') || lowerField.includes('key')) {
            const takeaways = [
                'Strategic advantage through innovation',
                'Proven ROI within 6 months',
                'Comprehensive support included',
                'Future-ready technology stack',
                'Industry-leading performance'
            ]
            return takeaways.slice(0, itemCount)
        }

        // Generate generic array items
        const items = []
        for (let i = 0; i < itemCount; i++) {
            if (itemSchema) {
                items.push(generateFieldValue(`${fieldName}Item`, itemSchema, layoutName))
            } else {
                items.push(`Sample item ${i + 1}`)
            }
        }
        return items
    }

    const generateObjectValue = (fieldName: string, fieldSchema: any, layoutName: string): any => {
        const shape = fieldSchema._def?.shape
        if (!shape) return {}

        const obj: any = {}
        for (const [key, subSchema] of Object.entries(shape)) {
            obj[key] = generateFieldValue(key, subSchema, layoutName)
        }
        return obj
    }

    const loadAllLayouts = async () => {
        try {
            setLoading(true)
            setError(null)

            const response = await fetch('/api/layouts')
            if (!response.ok) {
                throw new Error('Failed to fetch layout files')
            }

            const layoutFiles: string[] = await response.json()
            const loadedLayouts: LayoutInfo[] = []

            for (const fileName of layoutFiles) {
                try {
                    const layoutName = fileName.replace('.tsx', '').replace('.ts', '')
                    const module = await import(`../../components/layouts/${layoutName}`)

                    if (!module.default) {
                        toast({
                            title: `${layoutName} has no default export`,
                            description: 'Please ensure the layout file exports a default component',

                        })
                        console.warn(`${layoutName} has no default export`)
                        continue
                    }

                    if (!module.Schema) {
                        toast({
                            title: `${layoutName} is missing required Schema export`,
                            description: 'Please ensure the layout file exports a Schema',
                            variant: 'destructive'
                        })
                        console.error(`${layoutName} is missing required Schema export`)
                        continue
                    }

                    const sampleData = generateSampleDataFromSchema(module.Schema, layoutName)

                    loadedLayouts.push({
                        name: layoutName,
                        component: module.default,
                        schema: module.Schema,
                        sampleData,
                        fileName
                    })

                } catch (importError) {
                    console.error(`Failed to import ${fileName}:`, importError)

                    // Try alternative import path
                    try {
                        const layoutName = fileName.replace('.tsx', '').replace('.ts', '')
                        const module = await import(`@/components/layouts/${layoutName}`)

                        if (module.default && module.Schema) {
                            const sampleData = generateSampleDataFromSchema(module.Schema, layoutName)
                            loadedLayouts.push({
                                name: layoutName,
                                component: module.default,
                                schema: module.Schema,
                                sampleData,
                                fileName
                            })
                        } else {
                            console.error(`${layoutName} is missing required exports (default component or Schema)`)
                        }
                    } catch (altError) {
                        console.error(`Alternative import also failed for ${fileName}:`, altError)
                    }
                }
            }

            if (loadedLayouts.length === 0) {
                setError('No valid layouts found. Make sure your layout files export both a default component and a Schema.')
            } else {
                setLayouts(loadedLayouts)
            }

        } catch (error) {
            console.error('Error loading layouts:', error)
            setError(error instanceof Error ? error.message : 'Failed to load layouts')
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        loadAllLayouts()
    }, [])

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-screen">
                <div className="text-xl">Loading layouts...</div>
            </div>
        )
    }

    if (error) {
        return (
            <div className="flex items-center justify-center min-h-screen">
                <div className="text-xl text-red-500">Error: {error}</div>
            </div>
        )
    }

    if (layouts.length === 0) {
        return (
            <div className="flex items-center justify-center min-h-screen">
                <div className="text-xl text-gray-500">No layouts found</div>
            </div>
        )
    }

    const CurrentLayoutComponent = layouts[currentLayout]?.component

    return (
        <div className="min-h-screen bg-gray-100">
            {/* Navigation */}
            <div className="bg-white shadow-md p-4">
                <div className="max-w-6xl mx-auto flex items-center justify-between">
                    <h1 className="text-2xl font-bold text-gray-800">Layout Preview</h1>
                    <div className="flex items-center space-x-4">
                        <select
                            value={currentLayout}
                            onChange={(e) => setCurrentLayout(Number(e.target.value))}
                            className="px-4 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                        >
                            {layouts.map((layout, index) => (
                                <option key={index} value={index}>
                                    {layout.name.replace('Layout', '').replace(/([A-Z])/g, ' $1').trim()}
                                </option>
                            ))}
                        </select>
                        <div className="flex space-x-2">
                            <button
                                onClick={() => setCurrentLayout((prev) => Math.max(0, prev - 1))}
                                disabled={currentLayout === 0}
                                className="px-4 py-2 bg-blue-600 text-white rounded-md disabled:bg-gray-300 hover:bg-blue-700"
                            >
                                Previous
                            </button>
                            <button
                                onClick={() => setCurrentLayout((prev) => Math.min(layouts.length - 1, prev + 1))}
                                disabled={currentLayout === layouts.length - 1}
                                className="px-4 py-2 bg-blue-600 text-white rounded-md disabled:bg-gray-300 hover:bg-blue-700"
                            >
                                Next
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            {/* Layout Display */}
            <div className="relative max-w-6xl mx-auto">
                {CurrentLayoutComponent && (
                    <CurrentLayoutComponent data={layouts[currentLayout].sampleData} />
                )}
            </div>

            {/* Layout Info */}
            <div className="bg-white border-t p-4">
                <div className="max-w-6xl mx-auto">
                    <h3 className="text-lg font-semibold mb-2">
                        Current Layout: {layouts[currentLayout]?.name}
                    </h3>
                    <p className="text-gray-600 mb-4">
                        {currentLayout + 1} of {layouts.length} layouts ({layouts[currentLayout]?.fileName})
                    </p>
                    {layouts[currentLayout]?.sampleData && (
                        <details className="bg-gray-50 p-4 rounded-md">
                            <summary className="cursor-pointer font-medium text-gray-700">
                                Sample Data Structure
                            </summary>
                            <pre className="mt-2 text-sm text-gray-600 overflow-x-auto">
                                {JSON.stringify(layouts[currentLayout].sampleData, null, 2)}
                            </pre>
                        </details>
                    )}
                </div>
            </div>
        </div>
    )
}

export default LayoutPreview
