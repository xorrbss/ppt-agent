export interface LayoutGroup {
    id: string;
    name: string;
    description: string;
    ordered: boolean;
    isDefault?: boolean;
    slides: string[];
}

export const ProfessionalLayoutGroup: LayoutGroup = {
    id: 'professional',
    name: 'Professional',
    description: 'Clean, corporate designs perfect for business presentations',
    ordered: true,
    isDefault: true,
    slides: [
        'first-slide', 
        'content-slide', 
        'bullet-point-slide', 
        'comparison-slide', 
        'type4-slide', 
        'statistics-slide',
        'team-slide',
        'quote-slide'
    ]
}

export const CreativeLayoutGroup: LayoutGroup = {
    id: 'creative',
    name: 'Creative',
    description: 'Vibrant, artistic layouts for innovative and creative presentations',
    ordered: false,
    slides: [
        'image-slide', 
        'icon-slide', 
        'card-slide', 
        'type1-slide',
        'type2-slide',
        'type3-slide',
        'process-slide'
    ]
}

export const ModernLayoutGroup: LayoutGroup = {
    id: 'modern',
    name: 'Modern',
    description: 'Contemporary designs with clean lines and sophisticated layouts',
    ordered: true,
    slides: [
        'type5-slide',
        'type6-slide', 
        'type7-slide',
        'type8-slide',
        'timeline-slide',
        'type2-timeline-slide',
        'number-box-slide'
    ]
}

export const MinimalLayoutGroup: LayoutGroup = {
    id: 'minimal',
    name: 'Minimal',
    description: 'Simple, focused layouts that emphasize content over decoration',
    ordered: false,
    slides: [
        'content-slide',
        'bullet-point-slide',
        'type2-numbered-slide',
        'quote-slide',
        'statistics-slide'
    ]
}

export const LayoutGroups = [
    ProfessionalLayoutGroup,
    CreativeLayoutGroup,
    ModernLayoutGroup,
    MinimalLayoutGroup
];

export const getDefaultLayoutGroup = (): LayoutGroup => {
    return LayoutGroups.find(group => group.isDefault) || ProfessionalLayoutGroup;
};

export const getAllLayouts = (): string[] => {
    const allLayouts = new Set<string>();
    LayoutGroups.forEach(group => {
        group.slides.forEach(slide => allLayouts.add(slide));
    });
    return Array.from(allLayouts);
};

export const getGroupByLayoutId = (layoutId: string): LayoutGroup | undefined => {
    return LayoutGroups.find(group => group.slides.includes(layoutId));
};


