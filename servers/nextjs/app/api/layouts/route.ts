import { NextResponse } from 'next/server'
import { promises as fs } from 'fs'
import path from 'path'

interface GroupSetting {
    description: string;
    ordered: boolean;
    isDefault?: boolean;
}

export async function GET() {
    try {
        // Get the path to the presentation-layouts directory
        const layoutsDirectory = path.join(process.cwd(), 'presentation-layouts')
        
        // Read all directories in the presentation-layouts directory
        const items = await fs.readdir(layoutsDirectory, { withFileTypes: true })
        
        // Filter for directories (layout groups) and exclude files
        const groupDirectories = items
            .filter(item => item.isDirectory())
            .map(dir => dir.name)
        
        const allLayouts: { groupName: string; files: string[]; settings: GroupSetting | null }[] = []
        
        // Scan each group directory for layout files and settings
        for (const groupName of groupDirectories) {
            try {
                const groupPath = path.join(layoutsDirectory, groupName)
                const groupFiles = await fs.readdir(groupPath)
                
                // Filter for .tsx files and exclude any non-layout files
                const layoutFiles = groupFiles.filter(file => 
                    file.endsWith('.tsx') && 
                    !file.startsWith('.') && 
                    !file.includes('.test.') &&
                    !file.includes('.spec.') &&
                    file !== 'setting.json'
                )
                
                // Read settings.json if it exists
                let settings: GroupSetting | null = null
                const settingsPath = path.join(groupPath, 'setting.json')
                try {
                    const settingsContent = await fs.readFile(settingsPath, 'utf-8')
                    settings = JSON.parse(settingsContent) as GroupSetting
                } catch (settingsError) {
                    console.warn(`No settings.json found for group ${groupName} or invalid JSON`)
                    // Provide default settings if setting.json is missing or invalid
                    settings = {
                        description: `${groupName} presentation layouts`,
                        ordered: false,
                        isDefault: false
                    }
                }
                
                if (layoutFiles.length > 0) {
                    allLayouts.push({
                        groupName: groupName,
                        files: layoutFiles,
                        settings: settings
                    })
                }
            } catch (error) {
                console.error(`Error reading group directory ${groupName}:`, error)
                // Continue with other groups even if one fails
            }
        }
      
        
        return NextResponse.json(allLayouts)
    } catch (error) {
        console.error('Error reading presentation-layouts directory:', error)
        return NextResponse.json(
            { error: 'Failed to read presentation-layouts directory' },
            { status: 500 }
        )
    }
} 