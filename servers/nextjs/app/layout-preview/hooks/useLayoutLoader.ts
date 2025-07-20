'use client'
import { useState, useEffect, useCallback } from 'react'
import { useLayoutWatcher } from './useLayoutWatcher'
import { LayoutInfo, LayoutGroup, GroupedLayoutsResponse, GroupSetting } from '../types'
import { toast } from 'sonner'

interface UseLayoutLoaderReturn {
    layoutGroups: LayoutGroup[]
    layouts: LayoutInfo[]
    loading: boolean
    error: string | null
    retry: () => void
    isWatcherConnected: boolean
}

export const useLayoutLoader = (): UseLayoutLoaderReturn => {
    const [layoutGroups, setLayoutGroups] = useState<LayoutGroup[]>([])
    const [layouts, setLayouts] = useState<LayoutInfo[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)

    const loadAllLayouts = useCallback(async () => {
        try {
            setLoading(true)
            setError(null)

            // Always fetch fresh data for layout preview
            const response = await fetch('/api/layouts', {
                cache: 'no-cache',
                headers: { 'Cache-Control': 'no-cache' }
            })
            
            if (!response.ok) {
                toast.error('Error loading layouts', {
                    description: response.statusText,       
                })
                return
            }
            const groupedLayoutsData: GroupedLayoutsResponse[] = await response.json()
            const loadedGroups: LayoutGroup[] = []
            const allLayouts: LayoutInfo[] = []

            for (const groupData of groupedLayoutsData) {
                const groupLayouts: LayoutInfo[] = []

                const groupSettings: GroupSetting = groupData.settings ? groupData.settings : {
                    description: `${groupData.groupName} presentation layouts`,
                    ordered: false,
                    isDefault: false
                }

                for (const fileName of groupData.files) {
                    try {
                        const layoutName = fileName.replace('.tsx', '').replace('.ts', '')
                        
                        // Always fresh import for hot reloading - no caching
                        const module = await import(`@/presentation-layouts/${groupData.groupName}/${layoutName}`)

                        if (!module.default) {
                            toast.error(`${layoutName} has no default export`, {
                                description: 'Please ensure the layout file exports a default component',
                            })
                            console.warn(`${layoutName} has no default export`)
                            continue
                        }

                        if (!module.Schema) {
                            toast.error(`${layoutName} is missing required Schema export`, {
                                description: 'Please ensure the layout file exports a Schema',
                            })
                            console.error(`${layoutName} is missing required Schema export`)
                            continue
                        }

                        // Use empty object to let schema apply its default values
                        const sampleData = module.Schema.parse({})
                        console.log('🔍 Sample data:', sampleData)
                        const layoutId = module.layoutId || layoutName.toLowerCase().replace(/layout$/, '')

                        const layoutInfo: LayoutInfo = {
                            name: layoutName,
                            component: module.default,
                            schema: module.Schema,
                            sampleData,
                            fileName,
                            groupName: groupData.groupName,
                            layoutId
                        }

                        groupLayouts.push(layoutInfo)
                        allLayouts.push(layoutInfo)

                    } catch (importError) {
                        console.error(`Failed to import ${fileName} from ${groupData.groupName}:`, importError)
                    }
                }

                if (groupLayouts.length > 0) {
                    loadedGroups.push({
                        groupName: groupData.groupName,
                        layouts: groupLayouts,
                        settings: groupSettings
                    })
                }
            }

            if (allLayouts.length === 0) {
                toast.error('No valid layouts found', {
                    description: 'Make sure your layout files export both a default component and a Schema.',
                })
                setError('No valid layouts found. Make sure your layout files export both a default component and a Schema.')
            } else {
                setLayoutGroups(loadedGroups)
                setLayouts(allLayouts)
                setError(null)
            }

        } catch (error) {
            console.error('Error loading layouts:', error)
            setError(error instanceof Error ? error.message : 'Failed to load layouts')
        } finally {
            setLoading(false)
        }
    }, []) 

    const handleLayoutChange = () => {
        console.log('🔄 Layout change detected, reloading...')
        setTimeout(() => {
            loadAllLayouts()
        }, 150)
    };

    // Setup file watcher for development
    const { isConnected: isWatcherConnected } = useLayoutWatcher({
        onLayoutChange: handleLayoutChange,
        enabled: process.env.NODE_ENV === 'development'
    })

    const retry = useCallback(() => {
        loadAllLayouts()
    }, [loadAllLayouts])

    useEffect(() => {
        loadAllLayouts()
    }, [loadAllLayouts])

    return {
        layoutGroups,
        layouts,
        loading,
        error,
        retry,
        isWatcherConnected
    }
} 