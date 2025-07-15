/**
 * Shared types for the Layout Preview system
 */

export interface LayoutInfo {
    name: string
    component: React.ComponentType<any>
    schema: any
    sampleData: any
    fileName: string
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