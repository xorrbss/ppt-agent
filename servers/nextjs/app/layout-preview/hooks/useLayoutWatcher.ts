'use client'
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

interface UseLayoutWatcherProps {
    onLayoutChange: () => void
    enabled?: boolean
}

export const useLayoutWatcher = ({ onLayoutChange, enabled = true }: UseLayoutWatcherProps) => {
    const wsRef = useRef<WebSocket | null>(null)
    const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)
    const [isConnected, setIsConnected] = useState(false)

    const connectWebSocket = () => {
        if (!enabled || typeof window === 'undefined') return

        try {
            // Get the current host (works in Docker and local development)
            const host = window.location.hostname
            const wsUrl = `ws://${host}:3001`
            
            console.log('🔄 Connecting to layout watcher:', wsUrl)
            
            // Create WebSocket connection to our layout watcher endpoint
            const ws = new WebSocket(wsUrl)
            wsRef.current = ws

            ws.onopen = () => {
                console.log('✅ Layout watcher connected successfully')
                setIsConnected(true)
                
                // Clear any existing reconnect timeout
                if (reconnectTimeoutRef.current) {
                    clearTimeout(reconnectTimeoutRef.current)
                    reconnectTimeoutRef.current = null
                }
            }

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data)
                    console.log('📨 WebSocket message received:', data)
                    
                    if (data.type === 'layout-change') {
                        console.log('🔥 Layout file changed:', data.file)
                        console.log('📁 Group:', data.groupName, 'File:', data.fileName)
                        
                        // Show toast notification
                        toast.success('Layout updated!', {
                            description: `${data.file} has been reloaded`,
                            duration: 2000,
                        })
                        
                        // Trigger reload
                        console.log('🚀 Triggering layout reload...')
                        onLayoutChange()
                    } else if (data.type === 'connected') {
                        console.log('✅ Layout watcher handshake complete')
                    }
                } catch (error) {
                    console.error('❌ Error parsing WebSocket message:', error)
                }
            }

            ws.onclose = (event) => {
                console.log('🔌 Layout watcher disconnected. Code:', event.code, 'Reason:', event.reason)
                setIsConnected(false)
                wsRef.current = null

                // Attempt to reconnect after 3 seconds if enabled
                if (enabled) {
                    console.log('⏰ Will attempt to reconnect in 3 seconds...')
                    reconnectTimeoutRef.current = setTimeout(() => {
                        console.log('🔄 Attempting to reconnect layout watcher...')
                        connectWebSocket()
                    }, 3000)
                }
            }

            ws.onerror = (error) => {
                console.error('❌ Layout watcher WebSocket error:', error)
                setIsConnected(false)
                ws.close()
            }

        } catch (error) {
            console.error('❌ Failed to create WebSocket connection:', error)
            setIsConnected(false)
        }
    }

    useEffect(() => {
        if (enabled) {
            console.log('🎯 Layout watcher hook enabled, connecting...')
            connectWebSocket()
        } else {
            console.log('⏸️ Layout watcher hook disabled')
        }

        return () => {
            if (wsRef.current) {
                console.log('🧹 Cleaning up WebSocket connection')
                wsRef.current.close()
                wsRef.current = null
            }
            if (reconnectTimeoutRef.current) {
                clearTimeout(reconnectTimeoutRef.current)
                reconnectTimeoutRef.current = null
            }
            setIsConnected(false)
        }
    }, [enabled])

    // Cleanup on component unmount
    useEffect(() => {
        return () => {
            console.log('🧹 Cleaning up WebSocket connection')
            if (wsRef.current) {
                wsRef.current.close()
            }
            if (reconnectTimeoutRef.current) {
                clearTimeout(reconnectTimeoutRef.current)
            }
            setIsConnected(false)
        }
    }, [])

    return {
        isConnected,
        reconnect: connectWebSocket
    }
} 