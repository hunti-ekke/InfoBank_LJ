async function loadOntology() {
    const container = document.getElementById('view-ontology');
    container.innerHTML = '<div class="absolute inset-0 flex items-center justify-center"><i class="fas fa-circle-notch fa-spin text-3xl text-blue-500"></i></div>';
    
    try {
        const r = await fetch(`${API}/ontology/${CURRENT_USER_ID}`);
        const data = await r.json();
        container.innerHTML = ""; 

        if (!data.nodes || data.nodes.length === 0) {
            container.innerHTML = '<div class="absolute inset-0 flex items-center justify-center text-gray-500">Upload documents to build your Ontology.</div>';
            return;
        }

        const width = container.clientWidth;
        const height = container.clientHeight;

        const svg = d3.select("#view-ontology").append("svg")
            .attr("width", "100%")
            .attr("height", "100%")
            .call(d3.zoom().scaleExtent([0.5, 3]).on("zoom", (e) => svgGroup.attr("transform", e.transform)));

        const svgGroup = svg.append("g");

        const simulation = d3.forceSimulation(data.nodes)
            .force("link", d3.forceLink(data.links).id(d => d.id).distance(150))
            .force("charge", d3.forceManyBody().strength(-400))
            .force("center", d3.forceCenter(width / 2, height / 2))
            .force("collide", d3.forceCollide().radius(d => 20 + (d.val * 5)));

        const link = svgGroup.append("g")
            .selectAll("line")
            .data(data.links)
            .join("line")
            .attr("stroke", "#94a3b8")
            .attr("stroke-opacity", 0.6)
            .attr("stroke-width", d => Math.sqrt(d.value) * 2);

        const node = svgGroup.append("g")
            .selectAll("g")
            .data(data.nodes)
            .join("g")
            .attr("class", "node")
            .call(d3.drag()
                .on("start", dragstarted)
                .on("drag", dragged)
                .on("end", dragended));

        node.append("circle")
            .attr("r", d => 15 + (d.val * 4))
            .attr("fill", "#3b82f6")
            .attr("stroke", "#ffffff")
            .attr("stroke-width", 3)
            .attr("class", "shadow-lg");

        node.append("text")
            .text(d => d.id)
            .attr("x", d => 20 + (d.val * 4))
            .attr("y", 5)
            .style("font-size", "14px")
            .style("font-weight", "600")
            .style("font-family", "Inter")
            .style("fill", "#1e293b")
            .style("pointer-events", "none");

        simulation.on("tick", () => {
            link
                .attr("x1", d => d.source.x)
                .attr("y1", d => d.source.y)
                .attr("x2", d => d.target.x)
                .attr("y2", d => d.target.y);

            node.attr("transform", d => `translate(${d.x},${d.y})`);
        });

        function dragstarted(event, d) {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x; d.fy = d.y;
        }
        function dragged(event, d) {
            d.fx = event.x; d.fy = event.y;
        }
        function dragended(event, d) {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null; d.fy = null;
        }
    } catch (error) {
        console.error("Ontology load failed", error);
        container.innerHTML = '<div class="absolute inset-0 flex items-center justify-center text-red-500">Failed to load Ontology.</div>';
    }
}