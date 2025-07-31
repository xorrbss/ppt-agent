import * as z from "zod";

export const ImageSchema = z.object({
    image_url_: z.url().meta({
        description: "URL to image",
    }),
    image_prompt_: z.string().meta({
        description: "Prompt used to generate the image",
    }).min(10).max(50),
})

export const IconSchema = z.object({
    icon_url_: z.string().meta({
        description: "URL to icon",
    }),
    icon_query_: z.string().meta({
        description: "Query used to search the icon",
    }).min(5).max(20),
})