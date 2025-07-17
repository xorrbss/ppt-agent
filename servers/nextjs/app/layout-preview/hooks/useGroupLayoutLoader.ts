'use client'
import { useState, useEffect } from 'react'

import { LayoutInfo, LayoutGroup, GroupedLayoutsResponse, GroupSetting } from '../types'
import { toast } from '@/hooks/use-toast'

interface UseGroupLayoutLoaderReturn {
    layoutGroup: LayoutGroup | null
    loading: boolean
    error: string | null
    retry: () => void
}

export const useGroupLayoutLoader = (groupSlug: string): UseGroupLayoutLoaderReturn => {
    const [layoutGroup, setLayoutGroup] = useState<LayoutGroup | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)

    const loadGroupLayouts = async () => {
        try {
            setLoading(true)
            setError(null)
            setLayoutGroup(null)

            const response = await fetch('/api/layouts')
            if (!response.ok) {
                toast({
                    title: 'Error loading layouts',
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
                    const module = await import(`@/presentation-layouts/${targetGroupData.groupName}/${layoutName}`)

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
                    const sampleData = module.Schema.parse({})

                    const layoutInfo: LayoutInfo = {
                        name: layoutName,
                        component: module.default,
                        schema: module.Schema,
                        sampleData,
                        fileName,
                        groupName: targetGroupData.groupName
                    }

                    groupLayouts.push(layoutInfo)

                } catch (importError) {
                    console.error(`Failed to import ${fileName} from ${targetGroupData.groupName}:`, importError)

                    // Try alternative import path
                    try {
                        const layoutName = fileName.replace('.tsx', '').replace('.ts', '')
                        const module = await import(`@/presentation-layouts/${targetGroupData.groupName}/${layoutName}`)

                        if (module.default && module.Schema) {
                            const sampleData = module.Schema.parse({})
                            const layoutInfo: LayoutInfo = {
                                name: layoutName,
                                component: module.default,
                                schema: module.Schema,
                                sampleData,
                                fileName,
                                groupName: targetGroupData.groupName
                            }
                            groupLayouts.push(layoutInfo)
                        } else {
                            console.error(`${layoutName} is missing required exports (default component or Schema)`)
                        }
                    } catch (altError) {
                        console.error(`Alternative import also failed for ${fileName} from ${targetGroupData.groupName}:`, altError)
                    }
                }
            }

            if (groupLayouts.length === 0) {
                toast({
                    title: 'No valid layouts found',
                    description: `No valid layouts found in "${groupSlug}" group.`,
                })
                setError(`No valid layouts found in "${groupSlug}" group.`)
            } else {
                setLayoutGroup({
                    groupName: targetGroupData.groupName,
                    layouts: groupLayouts,
                    settings: groupSettings
                })
                setError(null)
            }

        } catch (error) {
            console.error('Error loading group layouts:', error)
            setError(error instanceof Error ? error.message : 'Failed to load group layouts')
        } finally {
            setLoading(false)
        }
    }

    const retry = () => {
        loadGroupLayouts()
    }

    useEffect(() => {
        if (groupSlug) {
            loadGroupLayouts()
        }
    }, [groupSlug])

    return {
        layoutGroup,
        loading,
        error,
        retry
    }
} 