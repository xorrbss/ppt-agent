'use client'
import { useState, useEffect } from 'react'

import { LayoutInfo } from '../types'
import { toast } from '@/hooks/use-toast'

interface UseLayoutLoaderReturn {
    layouts: LayoutInfo[]
    loading: boolean
    error: string | null
    retry: () => void
}

export const useLayoutLoader = (): UseLayoutLoaderReturn => {
    const [layouts, setLayouts] = useState<LayoutInfo[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)

    const loadAllLayouts = async () => {
        try {
            setLoading(true)
            setError(null)

            const response = await fetch('/api/layouts')
            if (!response.ok) {
                toast({
                    title: 'Error loading layouts',
                    description: response.statusText,
                    
                })
                return
            }

            const layoutFiles: string[] = await response.json()
            const loadedLayouts: LayoutInfo[] = []

            for (const fileName of layoutFiles) {
                try {
                    const layoutName = fileName.replace('.tsx', '').replace('.ts', '')
                    const module = await import(`@/components/layouts/${layoutName}`)

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
                           

                        })
                        console.error(`${layoutName} is missing required Schema export`)
                        continue
                    }

                    // Use empty object to let schema apply its default values
                    // User will need to provide actual data when using the layouts
                    const sampleData = module.Schema.parse({})

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
                            // Use empty object to let schema apply its default values
                            const sampleData = module.Schema.parse({})
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
                toast({
                    title: 'No valid layouts found',
                    description: 'Make sure your layout files export both a default component and a Schema.',

                })
                setError('No valid layouts found. Make sure your layout files export both a default component and a Schema.')
            } else {
                setLayouts(loadedLayouts)
                setError(null)
            }

        } catch (error) {
            console.error('Error loading layouts:', error)
            setError(error instanceof Error ? error.message : 'Failed to load layouts')
        } finally {
            setLoading(false)
        }
    }

    const retry = () => {
        loadAllLayouts()
    }

    useEffect(() => {
        loadAllLayouts()
    }, [])

    return {
        layouts,
        loading,
        error,
        retry
    }
} 