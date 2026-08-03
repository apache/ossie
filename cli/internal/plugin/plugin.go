package plugin

import "errors"

// Plugin is the parsed and validated representation of a single installed plugin.
// Path is the absolute path to the plugin's installation directory on disk.
type Plugin struct {
	Path             string // absolute path to plugin dir on disk
	OSSIEPluginSpec  string
	OSSIESpecVersion string
	Name             string // required; the plugin's own identity, matched against --from/--to values
	Platform         string // optional; freeform display label for what the plugin converts to/from (e.g. "dbt Labs", "PowerBI")
	Setup            string // relative path to setup script; empty = no setup
	Convert          ConvertConfig
}

// ConvertConfig holds both conversion directions.
type ConvertConfig struct {
	ToOssie   Direction
	FromOssie Direction
}

// Direction describes one conversion direction.
type Direction struct {
	Invoke  []string // command + args; first element is the executable
	Accepts []string // file extensions e.g. [".yaml"]; populated on ToOssie only
}

// rawPlugin mirrors the on-disk plugin.yaml layout for YAML unmarshaling.
type rawPlugin struct {
	OSSIEPluginSpec  string `yaml:"ossie_plugin_spec"`
	OSSIESpecVersion string `yaml:"ossie_spec_version"`

	Name     string `yaml:"name"`
	Platform string `yaml:"platform"`

	Setup string `yaml:"setup"`

	Convert struct {
		ToOssie struct {
			Invoke  []string `yaml:"invoke"`
			Accepts []string `yaml:"accepts"`
		} `yaml:"to_ossie"`
		FromOssie struct {
			Invoke []string `yaml:"invoke"`
		} `yaml:"from_ossie"`
	} `yaml:"convert"`
}

// validate checks that all required fields are present.
// It performs presence checks only — not format validation.
func (r *rawPlugin) validate() error {
	switch {
	case r.OSSIEPluginSpec == "":
		return errors.New("missing required field: ossie_plugin_spec")
	case r.OSSIESpecVersion == "":
		return errors.New("missing required field: ossie_spec_version")
	case r.Name == "":
		return errors.New("missing required field: name")
	case len(r.Convert.ToOssie.Invoke) == 0:
		return errors.New("missing required field: convert.to_ossie.invoke")
	case len(r.Convert.ToOssie.Accepts) == 0:
		return errors.New("missing required field: convert.to_ossie.accepts")
	case len(r.Convert.FromOssie.Invoke) == 0:
		return errors.New("missing required field: convert.from_ossie.invoke")
	}
	return nil
}

// toPlugin maps a validated rawPlugin to the exported Plugin type.
// path is the absolute directory path of the plugin's installation.
func (r *rawPlugin) toPlugin(path string) *Plugin {
	return &Plugin{
		Path:             path,
		OSSIEPluginSpec:  r.OSSIEPluginSpec,
		OSSIESpecVersion: r.OSSIESpecVersion,
		Name:             r.Name,
		Platform:         r.Platform,
		Setup:            r.Setup,
		Convert: ConvertConfig{
			ToOssie: Direction{
				Invoke:  r.Convert.ToOssie.Invoke,
				Accepts: r.Convert.ToOssie.Accepts,
			},
			FromOssie: Direction{
				Invoke: r.Convert.FromOssie.Invoke,
			},
		},
	}
}
