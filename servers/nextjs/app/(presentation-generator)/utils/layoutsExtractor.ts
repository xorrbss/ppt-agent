import { zodToJsonSchema } from 'zod-to-json-schema';
import fs from 'fs';
import * as path from 'path';

interface LayoutInfo {
  id: string;
  name: string;
  description: string;
  json_schema: Record<string, any>;
}

interface LayoutGroup {
  id: string;
  ordered: boolean;
  slides: string[];
}

interface LayoutStructure {
  name: string;
  ordered: boolean;
  slides: LayoutInfo[];
}

// Cache for layouts to avoid repeated file system operations
let layoutsCache: LayoutStructure[] | null = null;

/**
 * Dynamically imports a layout file and extracts its schema and metadata
 */
async function extractLayoutFromFile(filePath: string, fileName: string): Promise<LayoutInfo | null> {
  try {
    // Import the layout module dynamically
    const module = await import(filePath);
    
    // Check if the module has a Schema export
    if (!module.Schema) {
      console.warn(`No Schema export found in ${fileName}`);
      return null;
    }

    // Extract layout metadata (optional)
    const layoutId = module.layoutId || fileName.replace(/\.tsx?$/, '').toLowerCase().replace(/layout$/, '');
    const layoutName = module.layoutName || fileName.replace(/\.tsx?$/, '').replace(/([A-Z])/g, ' $1').trim();
    const layoutDescription = module.layoutDescription || `${layoutName} layout for presentations`;

    // Convert Zod schema to JSON schema
    const jsonSchema = zodToJsonSchema(module.Schema, {
      name: `${layoutId}Schema`,
      $refStrategy: 'none'
    });

    return {
      id: layoutId,
      name: layoutName,
      description: layoutDescription,
      json_schema: jsonSchema
    };
  } catch (error: unknown) {
    console.error(`Error extracting layout from ${fileName}:`, error);
    const errorMessage = error instanceof Error ? error.message : 'Unknown error';
    throw new Error(`Failed to extract schema from ${fileName}: ${errorMessage}`);
  }
}

/**
 * Gets all layout files from the layouts directory
 */
async function getLayoutFiles(): Promise<string[]> {
     const layoutsDirectory = path.join(process.cwd(), 'components', 'layouts')
  
  if (! fs.existsSync(layoutsDirectory)) {
    throw new Error(`Layouts directory not found at ${layoutsDirectory}`);
  }

  const files = fs.readdirSync(layoutsDirectory)
  
  // Filter for TypeScript/TSX files, excluding layoutGroup.ts
  return files.filter(file => 
    (file.endsWith('.ts') || file.endsWith('.tsx')) && 
    file !== 'layoutGroup.ts' &&
    !file.startsWith('.')
  );
}

/**
 * Extracts layout groups from layoutGroup.ts file
 */
async function extractLayoutGroups(): Promise<LayoutGroup[]> {
  try {
    const layoutGroupPath = path.join(process.cwd(), 'components', 'layouts', 'layoutGroup.ts');
    
    if (!fs.existsSync(layoutGroupPath)) {
      throw new Error('layoutGroup.ts file not found in layouts directory');
    }

    const module = await import(layoutGroupPath);
    
    // Extract all exported layout groups
    const layoutGroups: LayoutGroup[] = [];
    
    Object.keys(module).forEach(key => {
      const exportedItem = module[key];
      
      // Check if it's a layout group object
      if (exportedItem && 
          typeof exportedItem === 'object' && 
          exportedItem.id && 
          Array.isArray(exportedItem.slides)) {
        
        layoutGroups.push({
          id: exportedItem.id,
          ordered: exportedItem.ordered || false,
          slides: exportedItem.slides
        });
      }
    });

    if (layoutGroups.length === 0) {
      throw new Error('No valid layout groups found in layoutGroup.ts');
    }

    return layoutGroups;
  } catch (error) {
    console.error('Error extracting layout groups:', error);
    throw error;
  }
}

/**
 * Maps layout information to layout groups
 */
function mapLayoutsToGroups(
  layoutInfos: LayoutInfo[], 
  layoutGroups: LayoutGroup[]
): LayoutStructure[] {
  return layoutGroups.map(group => {
    const groupSlides: LayoutInfo[] = [];
    
    // Map slides in the group to their layout info
    group.slides.forEach(slideId => {
      const layoutInfo = layoutInfos.find(layout => 
        layout.id === slideId || 
        layout.id.replace('-', '') === slideId.replace('-', '') ||
        layout.id.toLowerCase() === slideId.toLowerCase()
      );
      
      if (layoutInfo) {
        groupSlides.push(layoutInfo);
      } else {
        console.warn(`Layout info not found for slide ID: ${slideId}`);
      }
    });

    return {
      name: group.id,
      ordered: group.ordered,
      slides: groupSlides
    };
  });
}

/**
 * Main function to extract all layouts dynamically
 */
export async function extractLayouts(): Promise<LayoutStructure[]> {
  // Return cached layouts if available
  if (layoutsCache) {
    return layoutsCache;
  }

  try {
    // Get all layout files
    const layoutFiles = await getLayoutFiles();
    
    if (layoutFiles.length === 0) {
      throw new Error('No layout files found in the layouts directory');
    }

    // Extract layout information from each file
    const layoutPromises = layoutFiles.map(async (fileName) => {
      const filePath = path.join(process.cwd(), 'components', 'layouts', fileName);
      return extractLayoutFromFile(filePath, fileName);
    });

    const layoutResults = await Promise.all(layoutPromises);
    
    // Filter out null results (files without valid schemas)
    const validLayouts = layoutResults.filter((layout): layout is LayoutInfo => layout !== null);
    
    if (validLayouts.length === 0) {
      throw new Error('No valid schemas found in any layout files');
    }

    // Extract layout groups
    const layoutGroups = await extractLayoutGroups();
    
    // Map layouts to groups
    const mappedLayouts = mapLayoutsToGroups(validLayouts, layoutGroups);
    
    // Cache the results
    layoutsCache = mappedLayouts;
    
    return mappedLayouts;
  } catch (error) {
    console.error('Error extracting layouts:', error);
    throw error;
  }
}

/**
 * Clears the layouts cache (useful for development)
 */
export function clearLayoutsCache(): void {
  layoutsCache = null;
}

/**
 * Gets a specific layout by ID
 */
export async function getLayoutById(layoutId: string): Promise<LayoutInfo | null> {
  const layouts = await extractLayouts();
  
  for (const group of layouts) {
    const layout = group.slides.find(slide => slide.id === layoutId);
    if (layout) {
      return layout;
    }
  }
  
  return null;
}

/**
 * Gets all available layout IDs
 */
export async function getAllLayoutIds(): Promise<string[]> {
  const layouts = await extractLayouts();
  const ids: string[] = [];
  
  layouts.forEach(group => {
    group.slides.forEach(slide => {
      ids.push(slide.id);
    });
  });
  
  return ids;
} 