import { NextResponse } from 'next/server'
import { promises as fs } from 'fs'
import path from 'path'

export async function GET() {
    try {
        // Get the path to the layouts directory
        const layoutsDirectory = path.join(process.cwd(), 'components', 'layouts')
        
        // Read all files in the layouts directory
        const files = await fs.readdir(layoutsDirectory)
        
        // Filter for .tsx files and exclude any non-layout files
        const layoutFiles = files.filter(file => 
            file.endsWith('.tsx') && 
            !file.startsWith('.') && 
            !file.includes('.test.') &&
            !file.includes('.spec.')
        )
        
        return NextResponse.json(layoutFiles)
    } catch (error) {
        console.error('Error reading layouts directory:', error)
        return NextResponse.json(
            { error: 'Failed to read layouts directory' },
            { status: 500 }
        )
    }
} 