import { useState, useEffect } from "react";
import { toast } from "@/hooks/use-toast";
import { zodToJsonSchema } from "zod-to-json-schema";

interface LayoutInfo {
  id: string;
  name?: string;
  description?: string;
  json_schema: any;
}

// interface LayoutStructure {
//   name: string;
//   ordered: boolean;
//   slides: LayoutInfo[];
// }

const useLayoutSchema = () => {
  const [layoutSchema, setLayoutSchema] = useState<LayoutInfo[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadLayouts = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch('/api/layouts');
      const layoutFiles = await response.json();
      const layouts = await extractSchema(layoutFiles);
      // console.log(layouts);
      setLayoutSchema(layouts || []);
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load layouts';
      setError(errorMessage);
      console.error('Error loading layouts:', err);
    } finally {
      setLoading(false);
    }
  };

  // Auto-load layouts on mount
  useEffect(() => {
    loadLayouts();
  }, []);

  return { 
    layoutSchema, 
    setLayoutSchema, 
    loading, 
    error, 
    refetch: loadLayouts 
  };
};

export default useLayoutSchema;


const extractSchema = async (layoutFiles: string[])  => {
  const layouts: LayoutInfo[] = [];
 for (const fileName of layoutFiles) {
  try {
    const file = fileName.replace('.tsx', '').replace('.ts', '')
    const module = await import(`@/components/layouts/${file}`)
    if (!module.default) {
      toast({
        title: `${file} has no default export`,
        description: 'Please ensure the layout file exports a default component',
      })
      console.warn(`${file} has no default export`)
      return
    }
    if (!module.Schema) {
      toast({
        title: `${file} has no Schema export`,
        description: 'Please ensure the layout file exports a Schema',
      })
      console.warn(`${file} has no Schema export`)
      return
    }
    const layoutId = module.layoutId
    if(!layoutId) {
      toast({
        title: `${file} has no layoutId`,
        description: 'Please ensure the layout file exports a layoutId',
      })
      console.warn(`${file} has no layoutId`)
      return
    }
    const layoutName = module.layoutName 
    const layoutDescription = module.layoutDescription 
    const jsonSchema = zodToJsonSchema(module.Schema)
    const layout = {
      id: layoutId,
      name: layoutName,
      description: layoutDescription,
      json_schema: jsonSchema
    }
    layouts.push(layout)
  } catch (error) {
    console.error(`Error extracting schema for ${fileName}:`, error)
    return null
  }
 }
 return layouts
};
