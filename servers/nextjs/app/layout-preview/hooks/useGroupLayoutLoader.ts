'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import { useLayoutWatcher } from './useLayoutWatcher'
import { LayoutInfo, LayoutGroup, GroupedLayoutsResponse, GroupSetting } from '../types'
import { toast } from 'sonner'

interface UseGroupLayoutLoaderReturn {
    layoutGroup: LayoutGroup | null
    loading: boolean
    error: string | null
    retry: () => void
    isWatcherConnected: boolean
}

export const useGroupLayoutLoader = (groupSlug: string): UseGroupLayoutLoaderReturn => {
    const [layoutGroup, setLayoutGroup] = useState<LayoutGroup | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)
    
    const hasMountedRef = useRef(false)

    const loadGroupLayouts = useCallback(async () => {
        try {
            setLoading(true)
            setError(null)

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
            
            // Find the specific group by slug
            const targetGroupData = groupedLayoutsData.find(
                group => group.groupName.toLowerCase() === groupSlug.toLowerCase()
            )

            if (!targetGroupData) {
                setError(`Group "${groupSlug}" not found`)
                return
            }

            const groupLayouts: LayoutInfo[] = []

            // Use settings from setting.json or provide defaults
            const groupSettings: GroupSetting = targetGroupData.settings ? targetGroupData.settings : {
                description: `${targetGroupData.groupName} presentation layouts`,
                ordered: false,
                isDefault: false
            }

            for (const fileName of targetGroupData.files) {
                try {
                    const layoutName = fileName.replace('.tsx', '').replace('.ts', '')
                    
                    // Import the module to get exports
                    const module = await import(`@/presentation-layouts/${targetGroupData.groupName}/${layoutName}`)

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
                    const layoutId = module.layoutId || layoutName.toLowerCase().replace(/layout$/, '')
                    
                  
                    
                    const layoutInfo: LayoutInfo = {
                        name: layoutName,
                        component: module.default,
                        schema: module.Schema,
                        sampleData,
                        fileName,
                        groupName: targetGroupData.groupName,
                        layoutId
                    }

                    groupLayouts.push(layoutInfo)

                } catch (importError) {
                    console.error(`Failed to import ${fileName} from ${targetGroupData.groupName}:`, importError)
                }
            }

            if (groupLayouts.length === 0) {
                toast.error('No valid layouts found', {
                    description: `No valid layouts found in "${groupSlug}" group.`,
                })
                setError(`No valid layouts found in "${groupSlug}" group.`)
            } else {
                const group: LayoutGroup = {
                    groupName: targetGroupData.groupName,
                    layouts: groupLayouts,
                    settings: groupSettings
                }
                
                setLayoutGroup(group)
                setError(null)
            }

        } catch (error) {
            console.error('Error loading group layouts:', error)
            setError(error instanceof Error ? error.message : 'Failed to load group layouts')
        } finally {
            setLoading(false)
        }
    }, [groupSlug]) 

    const handleLayoutChange = useCallback(() => {
        console.log(`🔄 Layout change detected for group: ${groupSlug}, reloading...`)
        
        setTimeout(() => {
            loadGroupLayouts()
        }, 150)
    }, [loadGroupLayouts])

    // Setup file watcher for development
    const { isConnected: isWatcherConnected } = useLayoutWatcher({
        onLayoutChange: handleLayoutChange,
        enabled: process.env.NODE_ENV === 'development'
    })

    const retry = useCallback(() => {
        loadGroupLayouts()
    }, [loadGroupLayouts])

    useEffect(() => {
        if (groupSlug && !hasMountedRef.current) {
            hasMountedRef.current = true
            loadGroupLayouts()
        }
    }, [groupSlug, loadGroupLayouts])

    return {
        layoutGroup,
        loading,
        error,
        retry,
        isWatcherConnected
    }
} 