import React from 'react'
import * as z from "zod";
import { ImageSchema } from '@/presentation-layouts/defaultSchemes';

export const layoutId = 'team-slide'
export const layoutName = 'Team Slide'
export const layoutDescription = 'A slide with a title, subtitle, and team members'

const teamSlideSchema = z.object({
    title: z.string().min(3).max(100).default('Meet Our Team').meta({
        description: "Title of the slide",
    }),
    subtitle: z.string().min(3).max(150).optional().meta({
        description: "Optional subtitle or team description",
    }),
    teamMembers: z.array(z.object({
        name: z.string().min(2).max(100).meta({
            description: "Team member name",
        }),
        title: z.string().min(2).max(100).meta({
            description: "Job title or role",
        }),
        image: z.string().optional().meta({
            description: "URL to team member photo",
        }),
        bio: z.string().min(10).max(300).optional().meta({
            description: "Brief biography or description",
        }),
        email: z.email().optional().meta({
            description: "Contact email",
        }),
        linkedin: z.string().optional().meta({
            description: "LinkedIn profile URL",
        })
    })).min(1).max(6).default([
        {
            name: 'Sarah Johnson',
            title: 'Chief Executive Officer',
            image: 'https://images.unsplash.com/photo-1494790108755-2616b612b786?w=300&h=300&fit=crop&crop=face',
            bio: 'Strategic leader with 15+ years experience driving innovation and growth in technology companies.',
            email: 'sarah@company.com',
            linkedin: 'https://linkedin.com/in/sarahjohnson'
        },
        {
            name: 'Michael Chen',
            title: 'Chief Technology Officer',
            image: 'https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=300&h=300&fit=crop&crop=face',
            bio: 'Technology visionary specializing in scalable architecture and emerging technologies.',
            email: 'michael@company.com',
            linkedin: 'https://linkedin.com/in/michaelchen'
        },
        {
            name: 'Emma Rodriguez',
            title: 'Head of Design',
            image: 'https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=300&h=300&fit=crop&crop=face',
            bio: 'Creative director passionate about user-centered design and innovative digital experiences.',
            email: 'emma@company.com',
            linkedin: 'https://linkedin.com/in/emmarodriguez'
        }
    ]).describe('Team members (1-6 people)'),
    backgroundImage: ImageSchema.optional().meta({
        description: "Background image for the slide",
    }),
})

export const Schema = teamSlideSchema

export type TeamSlideData = z.infer<typeof teamSlideSchema>

interface TeamSlideLayoutProps {
    data?: Partial<TeamSlideData>
}

const TeamSlideLayout: React.FC<TeamSlideLayoutProps> = ({ data: slideData }) => {

    return (
        <div
            className="relative w-full aspect-[16/9] bg-white overflow-hidden shadow-2xl border border-slate-200 print:shadow-none print:border-gray-300"
            style={slideData?.backgroundImage ? {
                backgroundImage: `url("${slideData.backgroundImage.__image_url__}")`,
                backgroundSize: 'cover',
                backgroundPosition: 'center',
                backgroundRepeat: 'no-repeat'
            } : {}}
        >
            {/* Enhanced geometric background decoration */}
            <div className="absolute inset-0 opacity-[0.03]">
                <div className="absolute top-0 right-0 w-96 h-96 bg-blue-600 rounded-full transform translate-x-32 -translate-y-32 blur-3xl" />
                <div className="absolute bottom-0 left-0 w-64 h-64 bg-blue-600 rounded-full transform -translate-x-16 translate-y-16 blur-2xl" />
            </div>

            {/* Grid overlay for professional look */}
            <div className="absolute inset-0 opacity-[0.02]" style={{
                backgroundImage: `linear-gradient(0deg, rgba(0,0,0,0.1) 1px, transparent 1px),
                                 linear-gradient(90deg, rgba(0,0,0,0.1) 1px, transparent 1px)`,
                backgroundSize: '60px 60px'
            }} />

            <div className="relative z-10 flex flex-col h-full px-8 py-8">
                {/* Professional Header */}
                <header className="mb-6">
                    <h1 className={`text-4xl md:text-5xl font-bold mb-3 tracking-tight leading-tight break-words ${slideData?.backgroundImage?.__image_prompt__
                        ? 'text-white drop-shadow-lg'
                        : 'text-slate-900'
                        }`}>
                        <span className="bg-gradient-to-r from-blue-600 to-blue-800 bg-clip-text text-transparent">
                            {slideData?.title}
                        </span>
                    </h1>

                    {slideData?.subtitle && (
                        <p className={`text-xl font-light leading-relaxed break-words ${slideData?.backgroundImage?.__image_prompt__
                            ? 'text-slate-200 drop-shadow-md'
                            : 'text-slate-600'
                            }`}>
                            {slideData?.subtitle}
                        </p>
                    )}

                    <div className="relative mt-4">
                        <div className="w-32 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full shadow-lg" />
                        <div className="absolute inset-0 w-32 h-1 bg-gradient-to-r from-blue-600 to-blue-800 rounded-full blur-sm opacity-50" />
                    </div>
                </header>

                {/* Enhanced Team Grid */}
                <main className="flex-1 flex items-center justify-center">
                    <div className="bg-white/95 backdrop-blur-lg rounded-2xl p-8 shadow-2xl border border-white/50 w-full max-w-6xl relative overflow-hidden">
                        {/* Team grid with responsive layout */}
                        <div className={`grid gap-6 relative z-10 ${slideData?.teamMembers?.length === 1 ? 'grid-cols-1 max-w-md mx-auto' :
                            slideData?.teamMembers?.length === 2 ? 'grid-cols-1 md:grid-cols-2 max-w-4xl mx-auto' :
                                slideData?.teamMembers?.length === 3 ? 'grid-cols-1 md:grid-cols-3' :
                                    slideData?.teamMembers?.length === 4 ? 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4' :
                                        slideData?.teamMembers?.length === 5 ? 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5' :
                                            'grid-cols-1 md:grid-cols-2 lg:grid-cols-3'
                            }`}>
                            {slideData?.teamMembers?.map((member, index) => (
                                <div key={index} className="bg-white/95 backdrop-blur-lg rounded-2xl p-6 shadow-2xl border border-white/50 text-center relative overflow-hidden group hover:transform hover:scale-105 transition-all duration-300">
                                    {/* Card accent */}
                                    <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-blue-600 to-blue-800" />

                                    {/* Professional Avatar */}
                                    <div className="relative mb-4 mx-auto w-24 h-24 group-hover:scale-110 transition-transform duration-300">
                                        <div className="w-full h-full rounded-full overflow-hidden shadow-2xl border-4 border-white relative">
                                            {member.image ? (
                                                <img
                                                    src={member.image}
                                                    alt={member.name}
                                                    className="w-full h-full object-cover"
                                                />
                                            ) : (
                                                <div className="w-full h-full rounded-full bg-blue-600 flex items-center justify-center text-white font-bold text-xl shadow-xl border-4 border-white">
                                                    {member.name.split(' ').map(n => n[0]).join('')}
                                                </div>
                                            )}
                                            <div className="absolute inset-0 rounded-full bg-gradient-to-r from-blue-600 to-blue-800 opacity-20 group-hover:opacity-30 transition-opacity duration-300" />
                                        </div>
                                    </div>

                                    {/* Member Details */}
                                    <div className="space-y-2">
                                        <h3 className="text-lg font-bold text-slate-900 break-words leading-tight">
                                            {member.name}
                                        </h3>
                                        <p className="text-sm font-semibold mb-3 bg-gradient-to-r from-blue-600 to-blue-800 bg-clip-text text-transparent">
                                            {member.title}
                                        </p>

                                        {member.bio && (
                                            <p className="text-xs text-slate-600 leading-relaxed break-words">
                                                {member.bio}
                                            </p>
                                        )}

                                        {/* Contact Icons */}
                                        <div className="flex justify-center space-x-3 pt-3">
                                            {member.email && (
                                                <a
                                                    href={`mailto:${member.email}`}
                                                    className="p-2 rounded-full bg-slate-100 hover:bg-slate-200 text-blue-600 transition-all duration-200 hover:transform hover:scale-110"
                                                    title="Email"
                                                >
                                                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                                                        <path d="M2.003 5.884L10 9.882l7.997-3.998A2 2 0 0016 4H4a2 2 0 00-1.997 1.884z" />
                                                        <path d="M18 8.118l-8 4-8-4V14a2 2 0 002 2h12a2 2 0 002-2V8.118z" />
                                                    </svg>
                                                </a>
                                            )}

                                            {member.linkedin && (
                                                <a
                                                    href={member.linkedin}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    className="p-2 rounded-full bg-slate-100 hover:bg-slate-200 text-blue-600 transition-all duration-200 hover:transform hover:scale-110"
                                                    title="LinkedIn"
                                                >
                                                    <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                                                        <path fillRule="evenodd" d="M16.338 16.338H13.67V12.16c0-.995-.017-2.277-1.387-2.277-1.39 0-1.601 1.086-1.601 2.207v4.248H8.014v-8.59h2.559v1.174h.037c.356-.675 1.227-1.387 2.526-1.387 2.703 0 3.203 1.778 3.203 4.092v4.711zM5.005 6.575a1.548 1.548 0 11-.003-3.096 1.548 1.548 0 01.003 3.096zm-1.337 9.763H6.34v-8.59H3.667v8.59zM17.668 1H2.328C1.595 1 1 1.581 1 2.298v15.403C1 18.418 1.595 19 2.328 19h15.34c.734 0 1.332-.582 1.332-1.299V2.298C19 1.581 18.402 1 17.668 1z" clipRule="evenodd" />
                                                    </svg>
                                                </a>
                                            )}
                                        </div>
                                    </div>

                                    {/* Background decoration */}
                                    <div className="absolute bottom-0 right-0 w-12 h-12 bg-gradient-to-tl from-blue-600 to-blue-800 opacity-5 rounded-tl-full" />
                                </div>
                            ))}
                        </div>
                    </div>
                </main>
            </div>

            {/* Enhanced decorative accent */}
            <div className="absolute bottom-0 left-0 right-0 h-3 bg-gradient-to-r from-blue-600 to-blue-800 shadow-lg">
                <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/30 to-transparent" />
                <div className="absolute inset-0 bg-gradient-to-r from-blue-600 to-blue-800 blur-sm opacity-50" />
            </div>

            {/* Professional corner accents */}
            <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-to-bl from-blue-600 to-blue-800 opacity-5 rounded-bl-full" />
        </div>
    )
}

export default TeamSlideLayout 