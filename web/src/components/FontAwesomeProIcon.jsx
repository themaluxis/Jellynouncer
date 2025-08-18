import React from 'react';

/**
 * Font Awesome Pro Icon Component
 * 
 * Uses Font Awesome Pro 5.15.4 CSS classes directly
 * Supports all Pro styles: solid, regular, light, duotone, brands
 * 
 * @param {string} icon - Icon name (e.g., 'home', 'server', 'bell')
 * @param {string} style - Icon style: 'solid', 'regular', 'light', 'duotone', 'brands' (default: 'solid')
 * @param {string} className - Additional CSS classes
 * @param {string} size - Size classes: 'xs', 'sm', 'lg', '2x', '3x', '4x', '5x', '6x', '7x', '8x', '9x', '10x'
 * @param {boolean} spin - Spinning animation
 * @param {boolean} pulse - Pulse animation
 * @param {boolean} fixedWidth - Fixed width icon
 * @param {string} color - Text color class (e.g., 'text-purple-500')
 */
const FontAwesomeProIcon = ({ 
  icon, 
  style = 'solid', 
  className = '', 
  size = null,
  spin = false,
  pulse = false,
  fixedWidth = false,
  color = null,
  ...props 
}) => {
  // Map style to Font Awesome prefix
  const styleMap = {
    solid: 'fas',
    regular: 'far',
    light: 'fal',
    duotone: 'fad',
    brands: 'fab'
  };

  const prefix = styleMap[style] || 'fas';
  
  // Build class list
  const classes = [
    prefix,
    `fa-${icon}`,
    size && `fa-${size}`,
    spin && 'fa-spin',
    pulse && 'fa-pulse',
    fixedWidth && 'fa-fw',
    color,
    className
  ].filter(Boolean).join(' ');

  return <i className={classes} {...props} />;
};

// Convenience components for each style
export const Icon = FontAwesomeProIcon;
export const IconSolid = (props) => <FontAwesomeProIcon {...props} style="solid" />;
export const IconRegular = (props) => <FontAwesomeProIcon {...props} style="regular" />;
export const IconLight = (props) => <FontAwesomeProIcon {...props} style="light" />;
export const IconDuotone = (props) => <FontAwesomeProIcon {...props} style="duotone" />;
export const IconBrands = (props) => <FontAwesomeProIcon {...props} style="brands" />;

export default FontAwesomeProIcon;