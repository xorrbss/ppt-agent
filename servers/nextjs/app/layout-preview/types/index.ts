/**
 * Shared types for the Layout Preview system
 */

export interface LayoutInfo {
    name: string
    component: React.ComponentType<any>
    schema: any
    sampleData: any
    fileName: string
    group: string
}

export interface GroupSetting {
    id: string;
    name: string;
    description: string;
    ordered: boolean;
    isDefault?: boolean;
}

export interface LayoutGroup {
    group: string
    layouts: LayoutInfo[]
    settings: GroupSetting
}

export interface GroupedLayoutsResponse {
    group: string
    files: string[]
    settings: GroupSetting | null
}

export interface LoadingState {
    loading: boolean
    error: string | null
}

export interface NavigationState {
    currentLayout: number
    totalLayouts: number
}

export type LoadingStateType = 'loading' | 'error' | 'empty'

export interface ComponentProps {
    className?: string
    children?: React.ReactNode
} 