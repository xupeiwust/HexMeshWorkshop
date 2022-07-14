#pragma once

#include <iostream>
#include <filesystem>
#include <fstream>
#include <nlohmann/json.hpp>
#include <string>

#include "paths.h"
#include "mesh_stats.h"

class InfoFile {

public:
    InfoFile(std::filesystem::path path) : _path(path) {

    }

    ~InfoFile() {
        std::ofstream ofs(_path);
        if(ofs.is_open()) {
            ofs << std::setw(4) << _json << std::endl;
            ofs.close();
        }
        else {
            std::cerr << "Error : unable to write " << _path.string() << std::endl;
        }
    }

    void add_entry(std::string key, std::string value) {
        _json[key] = value;
    }

    void add_entry(std::string key, int value) {
        _json[key] = value;
    }

    void add_entry(std::string key, double value) {
        _json[key] = value;
    }

    void add_entry(std::string key, std::string subkey, std::string value) {
        _json[key][subkey] = value;
    }

    void add_entry(std::string key, std::string subkey, int value) {
        _json[key][subkey] = value;
    }

    void add_entry(std::string key, std::string subkey, double value) {
        _json[key][subkey] = value;
    }

    void generated_by(std::string value) {
        _json["generated_by"] = value;
    }

    void comments(std::string value) {
        _json["comments"] = value;
    }

    void date(std::string value) {
        _json["date"] = value;
    }

private:
    std::filesystem::path _path;
    nlohmann::json _json;
};



class TetraMeshInfo : public InfoFile {

public:
    TetraMeshInfo(std::filesystem::path path) : InfoFile(path) {}

    void vertices(int value) {
        add_entry("vertices",value);
    }

    void tetrahedra(int value) {
        add_entry("tetrahedra",value);
    }

    void surface_vertices(int value) {
        add_entry("surface_vertices",value);
    }

    void surface_triangles(int value) {
        add_entry("surface_triangles",value);
    }

    //parameter of NETGEN & MeshGems
    void max_mesh_size_of(std::string algorithm, float value) {
        add_entry(algorithm,"max_mesh_size",value);
    }

    //parameter of GMSH
    void size_factor_of(std::string algorithm, float value) {
        add_entry(algorithm,"size_factor",value);
    }

    void fill_from(const TetraMeshStats& mesh_stats) {
        vertices(mesh_stats.get_nb_vertices());
        tetrahedra(mesh_stats.get_nb_tetrahedra());
        surface_vertices(mesh_stats.get_nb_surface_vertices());
        surface_triangles(mesh_stats.get_nb_surface_triangles());
    }

};


class LabelingInfo : public InfoFile {

public:
    LabelingInfo(std::filesystem::path path) : InfoFile(path) {}

    void fidelity(double value) {
        add_entry("fidelity",value);
    }

    void charts(int value) {
        add_entry("charts",value);
    }

    void boundaries(int value) {
        add_entry("boundaries",value);
    }

    void corners(int value) {
        add_entry("corners",value);
    }

    void turning_points(int value) {
        add_entry("turning-points",value);
    }

    void invalid_charts_score(int value) {
        add_entry("invalid_charts_score",value);
    }

    void invalid_boundaries_score(int value) {
        add_entry("invalid_boundaries_score",value);
    }

    void invalid_corners_score(int value) {
        add_entry("invalid_corners_score",value);
    }

    void total_invalidity_score(int value) {
        add_entry("total_invalidity_score",value);
    }

    void relaxed_invalid_corners_score(int value) {
        add_entry("relaxed_invalid_corners_score",value);
    }

    //parameter of graph-cut
    void compactness_of(std::string algorithm, int value) {
        add_entry(algorithm,"compactness",value);
    }

    //parameter of graph-cut
    void fidelity_of(std::string algorithm, int value) {
        add_entry(algorithm,"fidelity",value);
    }

    //return 1 = error, return 0 = worked
    bool fill_from(std::filesystem::path labeling_stats_file) {
        std::ifstream ifs(labeling_stats_file);
        if(!ifs.is_open()) {
            std::cerr << "Error : unable to read '" << labeling_stats_file.string() << "'" << std::endl;
            return 1;
        }

        std::string line, key, value;
        while (getline(ifs,line)) {
            if(line.empty()) {
                continue;
            }
            else if (line[0] == '#') {
                continue;
            }
            int separator_index = line.find('=');
            key = line.substr(0,separator_index);//what is before '='
            value = line.substr(separator_index+1);//what is after '='
            if(key == "fidelity") {
                fidelity(std::stod(value));
            }
            else if(key == "nb_corners") {
                corners(std::stoi(value));
            }
            else if(key == "nb_charts") {
                charts(std::stoi(value));
            }
            else if(key == "nb_boundaries") {
                boundaries(std::stoi(value));
            }
            else if(key == "nb_turning_points") {
                turning_points(std::stoi(value));
            }
            else if(key == "invalid_charts_score") {
                invalid_charts_score(std::stoi(value));
            }
            else if(key == "invalid_boundaries_score") {
                invalid_boundaries_score(std::stoi(value));
            }
            else if(key == "invalid_corners_score") {
                invalid_corners_score(std::stoi(value));
            }
            else if(key == "total_invalidity_score") {
                total_invalidity_score(std::stoi(value));
            }
            else if(key == "relaxed_invalid_corners_score") {
                relaxed_invalid_corners_score(std::stoi(value));
            }
        }
        return 0;
    }
};


class HexMeshInfo : public InfoFile {

public:
    HexMeshInfo(std::filesystem::path path) : InfoFile(path) {}

    void vertices(int value) {
        add_entry("vertices",value);
    }

    void hexahedra(int value) {
        add_entry("hexahedra",value);
    }

    void min_SJ(double value) {
        add_entry("min_SJ",value);
    }

    //parameter of polycube_withHexEx
    void scale_of(std::string algorithm, float value) {
        add_entry(algorithm,"scale",value);
    }

    void fill_from(const HexMeshStats& mesh_stats) {
        vertices(mesh_stats.get_nb_vertices());
        hexahedra(mesh_stats.get_nb_hexahedra());
        min_SJ(mesh_stats.get_min_SJ());
    }

};