package plugin

import "errors"

// Plugin is the parsed and validated representation of a single installed plugin.
// Path is the absolute path to the plugin's installation directory on disk.
type Plugin struct {
	Path             string // absolute path to plugin dir on disk
	OSSIEPluginSpec  string
	OSSIESpecVersion string
	Platform         Platform
	Setup            string // relative path to setup script; empty = no setup
	Convert          ConvertConfig
}

// Platform identifies the semantic platform this plugin handles.
type Platform struct {
	Name   string // required; matched against --from/--to values
	Vendor string // optional; human-readable
}

// ConvertConfig holds both conversion directions.
type ConvertConfig struct {
	ToOSI   Direction
	FromOSI Direction
}

// Direction describes one conversion direction.
type Direction struct {
	Invoke  []string // command + args; first element is the executable
	Accepts []string // file extensions e.g. [".yaml"]; populated on ToOSI only
}

// rawPlugin mirrors the on-disk plugin.yaml layout for YAML unmarshaling.
//
// ossie_* keys are the preferred (current brand) names. osi_* keys are
// deprecated aliases kept for backward compatibility with plugins written
// before the OSI → OSSIE rename. When both are present, ossie_* wins.
// TODO: remove osi_* fallback support in a future major version.
type rawPlugin struct {
	OSSIEPluginSpec  string `yaml:"ossie_plugin_spec"`
	OSSIESpecVersion string `yaml:"ossie_spec_version"`
	OSIPluginSpec    string `yaml:"osi_plugin_spec"`  // deprecated
	OSISpecVersion   string `yaml:"osi_spec_version"` // deprecated

	Platform struct {
		Name   string `yaml:"name"`
		Vendor string `yaml:"vendor"`
	} `yaml:"platform"`

	Setup string `yaml:"setup"`

	Convert struct {
		ToOSI struct {
			Invoke  []string `yaml:"invoke"`
			Accepts []string `yaml:"accepts"`
		} `yaml:"to_osi"`
		FromOSI struct {
			Invoke []string `yaml:"invoke"`
		} `yaml:"from_osi"`
	} `yaml:"convert"`
}

// validate checks that all required fields are present.
// It performs presence checks only — not format validation.
func (r *rawPlugin) validate() error {
	pluginSpec := r.OSSIEPluginSpec
	if pluginSpec == "" {
		pluginSpec = r.OSIPluginSpec
	}
	specVersion := r.OSSIESpecVersion
	if specVersion == "" {
		specVersion = r.OSISpecVersion
	}

	switch {
	case pluginSpec == "":
		return errors.New("missing required field: ossie_plugin_spec (or osi_plugin_spec)")
	case specVersion == "":
		return errors.New("missing required field: ossie_spec_version (or osi_spec_version)")
	case r.Platform.Name == "":
		return errors.New("missing required field: platform.name")
	case len(r.Convert.ToOSI.Invoke) == 0:
		return errors.New("missing required field: convert.to_osi.invoke")
	case len(r.Convert.ToOSI.Accepts) == 0:
		return errors.New("missing required field: convert.to_osi.accepts")
	case len(r.Convert.FromOSI.Invoke) == 0:
		return errors.New("missing required field: convert.from_osi.invoke")
	}
	return nil
}

// toPlugin maps a validated rawPlugin to the exported Plugin type.
// path is the absolute directory path of the plugin's installation.
func (r *rawPlugin) toPlugin(path string) *Plugin {
	pluginSpec := r.OSSIEPluginSpec
	if pluginSpec == "" {
		pluginSpec = r.OSIPluginSpec
	}
	specVersion := r.OSSIESpecVersion
	if specVersion == "" {
		specVersion = r.OSISpecVersion
	}
	return &Plugin{
		Path:             path,
		OSSIEPluginSpec:  pluginSpec,
		OSSIESpecVersion: specVersion,
		Platform: Platform{
			Name:   r.Platform.Name,
			Vendor: r.Platform.Vendor,
		},
		Setup: r.Setup,
		Convert: ConvertConfig{
			ToOSI: Direction{
				Invoke:  r.Convert.ToOSI.Invoke,
				Accepts: r.Convert.ToOSI.Accepts,
			},
			FromOSI: Direction{
				Invoke: r.Convert.FromOSI.Invoke,
			},
		},
	}
}
