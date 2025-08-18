# Font Awesome Icons

This project uses **Font Awesome Free 7.0.0** for all icons throughout the web interface.

## Features

Font Awesome Free 7 provides:
- **2,000+ free icons** in the latest version
- **Solid** style (filled icons) - Full set
- **Regular** style (outlined icons) - Partial set
- **Brands** style (company/social media logos) - Full set
- Improved performance and smaller file sizes
- Full open-source license (no authentication required)
- Included directly in the repository

## Location

Font Awesome 7 files are located at:
```
web/src/assets/fontawesome/
├── css/           # Stylesheets
│   └── all.min.css   # Main CSS file
├── webfonts/      # Font files
└── svgs/          # Individual SVG icons (optional)
```

## Usage

The project provides convenient icon components:

```jsx
import { Icon, IconSolid, IconRegular, IconBrands } from './components/FontAwesomeIcon';

// Basic usage
<Icon icon="home" />                    // Solid home icon (default)
<Icon icon="bell" style="regular" />    // Regular bell icon
<Icon icon="github" style="brands" />   // GitHub brand icon

// With sizing
<Icon icon="server" size="2x" />        // 2x size
<Icon icon="database" size="lg" />      // Large size

// With animations
<Icon icon="spinner" spin />            // Spinning animation
<Icon icon="heart" pulse />             // Pulse animation

// With custom classes
<Icon icon="check" className="text-green-500" />

// Convenience components
<IconSolid icon="save" />               // Explicitly solid
<IconRegular icon="calendar" />         // Explicitly regular
<IconBrands icon="discord" />           // Explicitly brands
```

## Available Icons

Browse all available icons at: [fontawesome.com/search?o=r&m=free](https://fontawesome.com/search?o=r&m=free)

## Icon Styles

- **Solid** (`fas`) - Filled icons, best for primary actions and navigation
- **Regular** (`far`) - Outlined icons, good for secondary elements
- **Brands** (`fab`) - Social media and company logos

## Common Icons Used in This Project

| Icon | Name | Usage |
|------|------|-------|
| 🏠 | `home` | Dashboard/Overview |
| ⚙️ | `cogs` | Settings/Configuration |
| 📄 | `file-code` | Templates/Code files |
| 🔍 | `search` | Search/Logs |
| 💾 | `save` | Save action |
| ↻ | `sync` | Refresh/Sync |
| 📊 | `chart-line` | Analytics/Stats |
| 🖥️ | `server` | Server status |
| 🎬 | `film` | Movies |
| 📺 | `tv` | TV Shows |
| 🎵 | `music` | Music/Audio |
| 🔒 | `lock` | Security/Login |
| 👤 | `user` | User profile |
| ⚠️ | `exclamation-triangle` | Warning |
| ℹ️ | `info-circle` | Information |
| ✅ | `check-circle` | Success |
| 🐛 | `bug` | Debug/Logs |

## Migrating from Older Versions

If you're updating from an older version:
- Font Awesome 7 maintains backward compatibility with v6 and v5
- Some icon names may have changed (check the Font Awesome website)
- The CSS classes remain the same (`fas`, `far`, `fab`)

## License

Font Awesome Free is licensed under:
- **Icons**: CC BY 4.0 License (attribution required for icons)
- **Fonts**: SIL OFL 1.1 License  
- **Code**: MIT License

This means it's completely free to use in any project, including commercial projects.

## Credits

Font Awesome Free 7.0.0 by [@fontawesome](https://fontawesome.com) - [License](https://fontawesome.com/license/free)