export default {
 source: {type: 'plantuml', path: './docs/architecture/container.puml'},
 rules: {acyclic:true,crud:true,acl:true,dbPerService:true,cohesion:false,stableDependencies:false,commonReuse:false,apiGateway:false}
};
